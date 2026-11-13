"""SQLite storage, encrypted. Everything lives in budget.db in the "data" folder next to the program (see paths.py).

The file is encrypted with SQLCipher (AES-256). Nothing can open it until you unlock Budget Tool with your password
(security.py hands the key to set_key()); while it's locked, connect() refuses.

Amounts in the register use spreadsheet sign: positive = deposit (money in),
negative = payment (money out). Plaid uses the opposite sign; plaid_client flips it.
"""
import os
import shutil
from datetime import datetime

try:
    from sqlcipher3 import dbapi2 as sqlite3
except ImportError:  # pragma: no cover - the updater installs it from requirements.txt
    raise RuntimeError("Budget Tool needs the sqlcipher3-wheels library to keep your data encrypted, and it isn't "
                       "installed. Run Update Budget Tool (in the Budget Tool folder) to install it.")

from paths import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "budget.db")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
SCHEMA_VERSION = 6
Error = sqlite3.Error
_KEY = [None]   # the 32-byte key while Budget Tool is unlocked; None while it's locked


class Locked(Exception):
    """Budget Tool is locked: nothing can be read until you enter your password."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    item_id          TEXT PRIMARY KEY,
    access_token     TEXT NOT NULL,
    institution_name TEXT,
    cursor           TEXT,
    last_synced      TEXT,
    error            TEXT
);
CREATE TABLE IF NOT EXISTS accounts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL UNIQUE,
    kind             TEXT NOT NULL DEFAULT 'checking',  -- checking|savings|credit|loan|cash|other
    goal             REAL,
    sort             INTEGER NOT NULL DEFAULT 0,
    closed           INTEGER NOT NULL DEFAULT 0,
    plaid_account_id TEXT UNIQUE,
    item_id          TEXT REFERENCES items(item_id) ON DELETE SET NULL,
    mask             TEXT,
    bank_current     REAL,
    bank_available   REAL,
    sync_from        TEXT,             -- ignore bank transactions before this date (YYYY-MM-DD)
    auto_opening     INTEGER NOT NULL DEFAULT 1  -- keep [Beginning Balance] matched to the bank
);
CREATE TABLE IF NOT EXISTS categories (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,                    -- unique among categories active in the same years
    kind         TEXT NOT NULL DEFAULT 'expense',  -- income | expense | transfer (non-budget)
    sort         INTEGER NOT NULL DEFAULT 0,
    active_from  INTEGER,                          -- first year it's used (NULL = always)
    active_until INTEGER,                          -- last year it's used (NULL = still in use)
    replaces     INTEGER,                          -- the category it took over from (planned for a new year)
    rollover     INTEGER NOT NULL DEFAULT 0        -- unspent budget carries into the next month (within a year)
);
CREATE TABLE IF NOT EXISTS budgets (
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    month       TEXT NOT NULL,              -- YYYY-MM
    amount      REAL NOT NULL,
    PRIMARY KEY (category_id, month)
);
CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    plaid_id        TEXT UNIQUE,
    import_key      TEXT UNIQUE,
    account_id      INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,          -- YYYY-MM-DD
    payee           TEXT,
    memo            TEXT,
    bank_name       TEXT,                   -- description as the bank sent it
    amount          REAL NOT NULL,          -- + deposit, - payment
    cleared         TEXT NOT NULL DEFAULT '',  -- '' | 'c' cleared | 'R' reconciled
    category_id     INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    category_locked INTEGER NOT NULL DEFAULT 0,  -- you picked it; sync won't change it
    payee_locked    INTEGER NOT NULL DEFAULT 0,  -- you renamed it; sync won't change it
    is_opening      INTEGER NOT NULL DEFAULT 0,  -- auto [Beginning Balance] for bank accounts
    pfc_primary     TEXT,                   -- Plaid's category, kept so we can re-sort later
    pfc_detailed    TEXT,
    merged          TEXT,                   -- JSON: your own entry that this bank transaction was combined with
    no_match        INTEGER NOT NULL DEFAULT 0  -- you said it's not the same: never combine it (duplicates.py)
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_account ON transactions(account_id, date);
-- rules: payee rules from older versions. No longer used (everything is categorized by hand); kept so
-- older databases open unchanged.
CREATE TABLE IF NOT EXISTS rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    match       TEXT NOT NULL UNIQUE,       -- lowercase text the bank description contains
    payee       TEXT,                       -- rename to this (optional)
    category_id INTEGER REFERENCES categories(id) ON DELETE CASCADE
);
-- A split transaction: its parts, each with its own category. The transaction itself then has no category.
CREATE TABLE IF NOT EXISTS splits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_id       INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    amount      REAL NOT NULL,              -- same sign as the transaction
    memo        TEXT
);
CREATE INDEX IF NOT EXISTS idx_splits_tx ON splits(tx_id);
-- What every category total is built from: each transaction once, or a split transaction's parts instead.
CREATE VIEW IF NOT EXISTS lines AS
    SELECT t.id AS tx_id, NULL AS split_id, t.account_id, t.date, t.amount, t.category_id, t.memo, t.is_opening, t.payee
      FROM transactions t WHERE NOT EXISTS (SELECT 1 FROM splits s WHERE s.tx_id = t.id)
    UNION ALL
    SELECT s.tx_id, s.id, t.account_id, t.date, s.amount, s.category_id, COALESCE(NULLIF(s.memo, ''), t.memo),
           t.is_opening, t.payee
      FROM splits s JOIN transactions t ON t.id = s.tx_id;
-- Bills and paychecks that repeat, for Coming up and Safe to spend on the Overview.
CREATE TABLE IF NOT EXISTS bills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    amount      REAL NOT NULL,              -- always positive
    kind        TEXT NOT NULL DEFAULT 'bill',  -- bill | income
    account_id  INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    every       TEXT NOT NULL DEFAULT 'month',  -- week | 2weeks | month | year
    next_date   TEXT NOT NULL               -- YYYY-MM-DD, the next time it's due
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

TRANSFER = "[Transfer]"
BEGINNING = "[Beginning Balance]"

DEFAULT_CATEGORIES = (
    [(n, "income") for n in ["Wages & Tips", "Interest Income", "Dividends", "Refunds/Reimbursements",
                             "Gifts Received", "Other Income"]]
    + [(n, "expense") for n in [
        "Groceries", "Dining", "Fuel", "Car Payment", "Car Insurance", "Car Repair / Licenses",
        "Mortgage / Rent", "Util. Electricity", "Util. Gas", "Util. Water", "Util. TV / Internet",
        "Phone Bill", "Subscriptions", "Health Insurance", "Doctor / Dentist", "Medicine", "Clothing",
        "Personal Supplies", "Home Supplies", "Fun / Entertainment", "Travel", "Gifts Given",
        "Education", "Debt", "Taxes", "Emergency Fund", "Retirement Fund", "Other Savings",
        "Discretionary", "Miscellaneous", "Uncategorised"]]
    + [(TRANSFER, "transfer"), (BEGINNING, "transfer")]
)


def set_key(key):
    _KEY[0] = bytes(key)


def clear_key():
    _KEY[0] = None


def unlocked():
    return _KEY[0] is not None


def _keyed(path, key=None):
    """Open an encrypted database file with the key (raw key: no extra key stretching on every connection)."""
    key = key or _KEY[0]
    if key is None:
        raise Locked()
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.execute("PRAGMA cipher_log_level = NONE")  # a wrong key isn't worth a console error
    except sqlite3.Error:
        pass
    conn.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
    return conn


def connect():
    conn = _keyed(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def is_plaintext(path):
    """An ordinary (unencrypted) SQLite file, from before Budget Tool had a password?"""
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def export_encrypted(conn, path, key=None):
    """Write an encrypted copy of conn's database (with the same key) to path."""
    key = key or _KEY[0]
    conn.execute("ATTACH DATABASE ? AS copy KEY ?", (path, f"x'{key.hex()}'"))
    try:
        conn.execute("SELECT sqlcipher_export('copy')")
        conn.execute("PRAGMA copy.user_version = " + str(int(conn.execute("PRAGMA user_version").fetchone()[0])))
    finally:
        conn.execute("DETACH DATABASE copy")


def verify(path, key=None):
    """Raise if the file isn't a healthy database that opens with the key."""
    conn = _keyed(path, key)
    try:
        ok = conn.execute("PRAGMA quick_check").fetchone()[0]
    except sqlite3.DatabaseError:
        raise ValueError("That file doesn't open with your password, or it's damaged.")
    finally:
        conn.close()
    if ok != "ok":
        raise ValueError(f"That file is damaged ({ok}).")


def restore_from(path):
    """Copy a backup's contents into the open budget (SQLite's backup API, safe while the app is running)."""
    src = _keyed(path)
    dst = connect()
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    init()


def _plain(path):
    """Open an unencrypted database with SQLCipher (no key given = no encryption)."""
    return sqlite3.connect(path, timeout=30)


def encrypt_existing(key):
    """First password: encrypt the budget you already have, in place. Older unencrypted copies Budget Tool made
    while upgrading (budget.db.v1-backup-..., budget.db.v2-backup-...) become encrypted backups in data\\backups.
    The unencrypted files are then deleted."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    jobs = []
    if is_plaintext(DB_PATH):
        jobs.append((DB_PATH, DB_PATH + ".encrypting"))
    for name in sorted(os.listdir(DATA_DIR)):
        if name.startswith("budget.db.v") and "-backup-" in name and is_plaintext(os.path.join(DATA_DIR, name)):
            old = os.path.join(DATA_DIR, name)
            stamp = datetime.fromtimestamp(os.path.getmtime(old)).strftime("%Y-%m-%d-%H%M%S")
            jobs.append((old, os.path.join(BACKUP_DIR, f"budget-{stamp}-older-version.db")))
    for src, dst in jobs:
        conn = _plain(src)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if os.path.exists(dst):
                os.remove(dst)
            export_encrypted(conn, dst, key)
            before = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for (t,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
        finally:
            conn.close()
        check = _keyed(dst, key)
        try:
            after = {t: check.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in before}
            healthy = check.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            check.close()
        if after != before or not healthy:
            os.remove(dst)
            raise RuntimeError("Couldn't encrypt your data safely, so nothing was changed. Please try again.")
        for extra in ("-wal", "-shm"):
            if os.path.exists(src + extra):
                os.remove(src + extra)
        if src == DB_PATH:
            os.replace(dst, DB_PATH)
        else:
            os.remove(src)


def _upgrade_backup(conn, label):
    """Before an upgrade changes the database: an encrypted copy in data\\backups."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copyfile(DB_PATH, os.path.join(BACKUP_DIR, datetime.now().strftime(f"budget-%Y-%m-%d-%H%M%S-{label}.db")))


def _migrate_from_v1(conn):
    """Version 1 kept bank data in a different shape. Keep the bank connections (so you don't
    have to reconnect) and let the next sync download the transactions again."""
    _upgrade_backup(conn, "before-v1-upgrade")
    conn.execute("PRAGMA foreign_keys = OFF")
    for t in ("transactions", "accounts", "categories", "rules"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.execute("UPDATE items SET cursor = NULL, last_synced = NULL")
    conn.execute("PRAGMA foreign_keys = ON")


def _migrate_categories_to_v3(conn):
    """Version 3: categories can be limited to certain years (planned changes for a new year), so a name only has
    to be unique within the years it's used. Rebuilds the categories table with the same ids (budgets and
    transactions keep pointing at the right categories). A copy of the old database is saved first."""
    _upgrade_backup(conn, "before-v3-upgrade")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript("""
        BEGIN;
        CREATE TABLE categories_v3 (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'expense',
            sort INTEGER NOT NULL DEFAULT 0, active_from INTEGER, active_until INTEGER, replaces INTEGER);
        INSERT INTO categories_v3 (id, name, kind, sort) SELECT id, name, kind, sort FROM categories;
        DROP TABLE categories;
        ALTER TABLE categories_v3 RENAME TO categories;
        COMMIT;
    """)
    conn.execute("PRAGMA foreign_keys = ON")


def init():
    conn = connect()
    try:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(accounts)")]
        if cols and "id" not in cols:
            _migrate_from_v1(conn)
        cat_cols = [r["name"] for r in conn.execute("PRAGMA table_info(categories)")]
        if cat_cols and "active_from" not in cat_cols:
            _migrate_categories_to_v3(conn)
        conn.executescript(SCHEMA)
        tx_cols = [r["name"] for r in conn.execute("PRAGMA table_info(transactions)")]
        for col, kind in (("merged", "TEXT"), ("no_match", "INTEGER NOT NULL DEFAULT 0")):  # version 5
            if col not in tx_cols:
                conn.execute(f"ALTER TABLE transactions ADD COLUMN {col} {kind}")
        cat_cols = [r["name"] for r in conn.execute("PRAGMA table_info(categories)")]
        if "rollover" not in cat_cols:  # version 6
            conn.execute("ALTER TABLE categories ADD COLUMN rollover INTEGER NOT NULL DEFAULT 0")
        set_setting(conn, "schema_version", str(SCHEMA_VERSION))
        if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO categories (name, kind, sort) VALUES (?, ?, ?)",
                [(n, k, i) for i, (n, k) in enumerate(DEFAULT_CATEGORIES)],
            )
        conn.commit()
    finally:
        conn.close()


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def category_id_by_name(conn, *names):
    """First of these category names that exists (case-insensitive), or None."""
    for name in names:
        row = conn.execute("SELECT id FROM categories WHERE lower(name) = lower(?)", (name,)).fetchone()
        if row:
            return row["id"]
    return None


def active_in(cat, year):
    """Is this category used in that year?"""
    return ((cat["active_from"] is None or cat["active_from"] <= year)
            and (cat["active_until"] is None or cat["active_until"] >= year))


def name_clash(conn, name, active_from=None, active_until=None, ignore_id=None):
    """Another category with this name (any capitalisation) used in any of the same years, or None."""
    lo, hi = active_from or 0, active_until or 9999
    for c in conn.execute("SELECT * FROM categories WHERE lower(name) = lower(?)", (name.strip(),)):
        if c["id"] == ignore_id:
            continue
        if (c["active_from"] or 0) <= hi and (c["active_until"] or 9999) >= lo:
            return c
    return None


def ensure_category(conn, name, kind):
    cid = category_id_by_name(conn, name)
    if cid is None:
        cid = conn.execute("INSERT INTO categories (name, kind, sort) VALUES (?, ?, 999)", (name, kind)).lastrowid
    return cid
