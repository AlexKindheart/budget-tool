"""SQLite storage for the budget app. Everything lives in %APPDATA%\\BudgetTool\\budget.db."""
import os
import sqlite3

from paths import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "budget.db")

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
    account_id TEXT PRIMARY KEY,
    item_id    TEXT NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
    name       TEXT,
    mask       TEXT,
    type       TEXT,
    subtype    TEXT,
    current    REAL,
    available  REAL,
    currency   TEXT
);
CREATE TABLE IF NOT EXISTS categories (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT NOT NULL UNIQUE,
    kind   TEXT NOT NULL DEFAULT 'expense',   -- expense | income | transfer
    budget REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id  TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL,
    date            TEXT NOT NULL,             -- YYYY-MM-DD
    name            TEXT,
    merchant        TEXT,
    amount          REAL NOT NULL,             -- Plaid sign: positive = money out
    pending         INTEGER NOT NULL DEFAULT 0,
    pfc_primary     TEXT,
    pfc_detailed    TEXT,
    category_id     INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    category_locked INTEGER NOT NULL DEFAULT 0  -- 1 = chosen by you, never overwritten by sync
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE TABLE IF NOT EXISTS rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    match       TEXT NOT NULL UNIQUE,          -- lowercase merchant/name
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# (name, kind) seeded on first run
DEFAULT_CATEGORIES = [
    ("Income", "income"),
    ("Groceries", "expense"),
    ("Dining & Drinks", "expense"),
    ("Rent & Utilities", "expense"),
    ("Transportation", "expense"),
    ("Shopping", "expense"),
    ("Entertainment", "expense"),
    ("Travel", "expense"),
    ("Medical", "expense"),
    ("Personal Care", "expense"),
    ("Home", "expense"),
    ("Services", "expense"),
    ("Bank Fees", "expense"),
    ("Gifts & Donations", "expense"),
    ("Other", "expense"),
    ("Transfers", "transfer"),
    ("Loan & Card Payments", "transfer"),
]

# Plaid personal_finance_category -> our category name. Detailed codes win over primary.
PFC_DETAILED_MAP = {
    "FOOD_AND_DRINK_GROCERIES": "Groceries",
}
PFC_PRIMARY_MAP = {
    "INCOME": "Income",
    "TRANSFER_IN": "Transfers",
    "TRANSFER_OUT": "Transfers",
    "LOAN_PAYMENTS": "Loan & Card Payments",
    "BANK_FEES": "Bank Fees",
    "ENTERTAINMENT": "Entertainment",
    "FOOD_AND_DRINK": "Dining & Drinks",
    "GENERAL_MERCHANDISE": "Shopping",
    "HOME_IMPROVEMENT": "Home",
    "MEDICAL": "Medical",
    "PERSONAL_CARE": "Personal Care",
    "GENERAL_SERVICES": "Services",
    "GOVERNMENT_AND_NON_PROFIT": "Gifts & Donations",
    "TRANSPORTATION": "Transportation",
    "TRAVEL": "Travel",
    "RENT_AND_UTILITIES": "Rent & Utilities",
}


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init():
    with connect() as conn:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO categories (name, kind) VALUES (?, ?)", DEFAULT_CATEGORIES
            )


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def category_for(conn, merchant_key, pfc_primary, pfc_detailed):
    """Pick a category id: your merchant rules first, then Plaid's category, then Other."""
    if merchant_key:
        row = conn.execute(
            "SELECT category_id FROM rules WHERE match = ?", (merchant_key,)
        ).fetchone()
        if row:
            return row["category_id"]
    name = PFC_DETAILED_MAP.get(pfc_detailed or "") or PFC_PRIMARY_MAP.get(pfc_primary or "", "Other")
    row = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
    if not row:
        row = conn.execute("SELECT id FROM categories WHERE name = 'Other'").fetchone()
    return row["id"] if row else None
