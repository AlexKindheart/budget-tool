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
UNCATEGORISED_NAMES = ["Uncategorised", "Uncategorized", "Miscellaneous", "Other"]

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

# Plaid category -> candidate category names, first one that exists wins.
# Detailed codes are checked before primary ones.
PLAID_CATEGORY_GUESSES = {
    "INCOME_WAGES": ["Wages & Tips", "Wages", "Salary", "Income"],
    "INCOME_DIVIDENDS": ["Dividends", "Other Income", "Income"],
    "INCOME_INTEREST_EARNED": ["Interest Income", "Other Income", "Income"],
    "INCOME_TAX_REFUND": ["Refunds/Reimbursements", "Other Income", "Income"],
    "INCOME": ["Other Income", "Income"],
    "TRANSFER_IN": [TRANSFER, "Transfers"],
    "TRANSFER_OUT": [TRANSFER, "Transfers"],
    "LOAN_PAYMENTS_CAR_PAYMENT": ["Car Payment", "Debt"],
    "LOAN_PAYMENTS_MORTGAGE_PAYMENT": ["Mortgage / Rent", "Debt"],
    "LOAN_PAYMENTS": ["Debt", "Loan & Card Payments", TRANSFER],
    "BANK_FEES_INTEREST_CHARGE": ["Debt", "Interest Expense", "Bank Fees"],
    "BANK_FEES": ["Bank Fees", "Miscellaneous"],
    "FOOD_AND_DRINK_GROCERIES": ["Groceries"],
    "FOOD_AND_DRINK": ["Dining", "Dining & Drinks", "Restaurants"],
    "TRANSPORTATION_GAS": ["Fuel", "Gas", "Transportation"],
    "TRANSPORTATION": ["Transportation", "Fuel"],
    "TRAVEL": ["Travel"],
    "RENT_AND_UTILITIES_RENT": ["Mortgage / Rent", "Rent"],
    "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY": ["Util. Electricity", "Utilities"],
    "RENT_AND_UTILITIES_WATER": ["Util. Water", "Utilities"],
    "RENT_AND_UTILITIES_INTERNET_AND_CABLE": ["Util. TV / Internet", "Utilities"],
    "RENT_AND_UTILITIES_TELEPHONE": ["Phone Bill", "Util. Phone(s)", "Utilities"],
    "RENT_AND_UTILITIES": ["Utilities", "Rent & Utilities"],
    "ENTERTAINMENT_TV_AND_MOVIES": ["Misc Subscriptions", "Subscriptions", "Fun / Entertainment"],
    "ENTERTAINMENT_MUSIC_AND_AUDIO": ["Important Subscripions", "Misc Subscriptions", "Subscriptions", "Fun / Entertainment"],
    "ENTERTAINMENT": ["Fun / Entertainment", "Entertainment", "Discretionary"],
    "GENERAL_MERCHANDISE_CLOTHING_AND_ACCESSORIES": ["Clothing"],
    "GENERAL_MERCHANDISE_ELECTRONICS": ["PC", "Discretionary"],
    "GENERAL_MERCHANDISE": ["Discretionary", "Shopping"],
    "HOME_IMPROVEMENT_FURNITURE": ["Furniture / Appliances", "Home Supplies"],
    "HOME_IMPROVEMENT": ["Home Supplies", "Home"],
    "MEDICAL_PHARMACIES_AND_SUPPLEMENTS": ["Medicine", "Doctor / Dentist", "Medical"],
    "MEDICAL": ["Doctor / Dentist", "Medical"],
    "PERSONAL_CARE": ["Personal Supplies", "Personal Care"],
    "GENERAL_SERVICES_EDUCATION": ["Education"],
    "GENERAL_SERVICES_INSURANCE": ["Car Insurance", "Insurance"],
    "GENERAL_SERVICES": ["Miscellaneous", "Services"],
    "GOVERNMENT_AND_NON_PROFIT_DONATIONS": ["Gifts Given", "Donations"],
    "GOVERNMENT_AND_NON_PROFIT_TAX_PAYMENT": ["Taxes"],
    "GOVERNMENT_AND_NON_PROFIT": ["Taxes", "Miscellaneous"],
}


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


def match_rule(conn, text):
    """Your longest matching rule for this bank description, or None."""
    text = (text or "").lower()
    if not text:
        return None
    best = None
    for r in conn.execute("SELECT * FROM rules"):
        if r["match"] and r["match"] in text and (best is None or len(r["match"]) > len(best["match"])):
            best = r
    return best


def guess_category(conn, pfc_primary, pfc_detailed, amount, account_kind):
    """Map Plaid's category onto your categories."""
    # Payment arriving on a credit card / loan = money moved from another account you own.
    if account_kind in ("credit", "loan") and amount > 0 and (pfc_primary or "").startswith(("LOAN_PAYMENTS", "TRANSFER_IN")):
        return category_id_by_name(conn, TRANSFER, "Transfers")
    for code in (pfc_detailed, pfc_primary):
        if code and code in PLAID_CATEGORY_GUESSES:
            cid = category_id_by_name(conn, *PLAID_CATEGORY_GUESSES[code])
            if cid:
                return cid
    return category_id_by_name(conn, *UNCATEGORISED_NAMES)


def reapply_rules(conn):
    """Re-sort bank transactions you haven't edited using your current rules and categories.
    Returns how many changed."""
    changed = 0
    rows = conn.execute(
        """SELECT t.*, a.kind AS account_kind FROM transactions t JOIN accounts a ON a.id = t.account_id
           WHERE t.plaid_id IS NOT NULL AND t.is_opening = 0
             AND (t.category_locked = 0 OR t.payee_locked = 0)""").fetchall()
    for t in rows:
        rule = match_rule(conn, f"{t['bank_name'] or ''} {t['payee'] or ''}")
        cat, payee = t["category_id"], t["payee"]
        if not t["category_locked"]:
            cat = (rule["category_id"] if rule and rule["category_id"] else None) or guess_category(
                conn, t["pfc_primary"], t["pfc_detailed"], t["amount"], t["account_kind"])
        if not t["payee_locked"] and rule and rule["payee"]:
            payee = rule["payee"]
        if cat != t["category_id"] or payee != t["payee"]:
            conn.execute("UPDATE transactions SET category_id = ?, payee = ? WHERE id = ?", (cat, payee, t["id"]))
            changed += 1
    return changed
