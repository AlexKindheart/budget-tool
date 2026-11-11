"""SQLite storage. Everything lives in %APPDATA%\\BudgetTool\\budget.db.

Amounts in the register use spreadsheet sign: positive = deposit (money in),
negative = payment (money out). Plaid uses the opposite sign; plaid_client flips it.
"""
import os
import shutil
import sqlite3
from datetime import datetime

from paths import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "budget.db")
SCHEMA_VERSION = 2

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
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'expense',   -- income | expense | transfer (non-budget)
    sort INTEGER NOT NULL DEFAULT 0
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
    pfc_detailed    TEXT
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


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _migrate_from_v1(conn):
    """Version 1 kept bank data in a different shape. Keep the bank connections (so you don't
    have to reconnect) and let the next sync download the transactions again."""
    backup = DB_PATH + datetime.now().strftime(".v1-backup-%Y%m%d-%H%M%S")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copyfile(DB_PATH, backup)
    conn.execute("PRAGMA foreign_keys = OFF")
    for t in ("transactions", "accounts", "categories", "rules"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.execute("UPDATE items SET cursor = NULL, last_synced = NULL")
    conn.execute("PRAGMA foreign_keys = ON")


def init():
    conn = connect()
    try:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(accounts)")]
        if cols and "id" not in cols:
            _migrate_from_v1(conn)
        conn.executescript(SCHEMA)
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


def ensure_category(conn, name, kind):
    cid = category_id_by_name(conn, name)
    if cid is None:
        cid = conn.execute("INSERT INTO categories (name, kind, sort) VALUES (?, ?, 999)", (name, kind)).lastrowid
    return cid
