"""Makes a made-up budget database for the tests: nothing in it is anyone's real data.

Three accounts (Checking, Savings with a goal, Visa) with about two years of paychecks, rent, groceries, card
payments and transfers to savings, plus this year's budget. It's an ordinary unencrypted SQLite file, like a budget
from before Budget Tool had a password, so the tests also cover encrypting an existing budget.
"""
import os
import random
import sqlite3
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def make(path, today=None):
    import db  # the app's own schema and starter categories
    today = today or date.today()
    rnd = random.Random(42)
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    conn.executescript(db.SCHEMA)
    conn.executemany("INSERT INTO categories (name, kind, sort) VALUES (?, ?, ?)",
                     [(n, k, i) for i, (n, k) in enumerate(db.DEFAULT_CATEGORIES)])
    cat = {n: i for i, n in conn.execute("SELECT id, name FROM categories")}
    acc = {}
    for i, (name, kind, goal) in enumerate([("Checking", "checking", None), ("Savings <Goal>", "savings", 5000),
                                            ("Visa", "credit", None)]):
        acc[name] = conn.execute("INSERT INTO accounts (name, kind, goal, sort, auto_opening) VALUES (?, ?, ?, ?, 0)",
                                 (name, kind, goal, i)).lastrowid

    def tx(account, d, payee, amount, category):
        conn.execute("INSERT INTO transactions (account_id, date, payee, amount, category_id, category_locked, "
                     "payee_locked) VALUES (?, ?, ?, ?, ?, 1, 1)",
                     (acc[account], d.isoformat(), payee, round(amount, 2), cat.get(category)))

    start = date(today.year - 2, today.month, 1)
    tx("Checking", start, "Opening", 1500, db.BEGINNING)
    tx("Savings <Goal>", start, "Opening", 800, db.BEGINNING)
    tx("Visa", start, "Opening", -250, db.BEGINNING)
    m = start
    while m <= today:
        for day in (1, 15):
            if date(m.year, m.month, day) <= today:
                tx("Checking", date(m.year, m.month, day), "Payday", 2100, "Wages & Tips")
        if date(m.year, m.month, 3) <= today:
            tx("Checking", date(m.year, m.month, 3), "Rent", -1200, "Mortgage / Rent")
        for week in range(4):
            d = date(m.year, m.month, 2 + week * 7)
            if d <= today:
                tx("Visa", d, "Groceries", -rnd.uniform(60, 140), "Groceries")
        if date(m.year, m.month, 20) <= today:
            owed = -conn.execute("SELECT SUM(amount) FROM transactions WHERE account_id = ?", (acc["Visa"],)).fetchone()[0]
            if owed > 0:
                tx("Checking", date(m.year, m.month, 20), "Card payment", -owed, db.TRANSFER)
                tx("Visa", date(m.year, m.month, 21), "Payment", owed, db.TRANSFER)
            tx("Checking", date(m.year, m.month, 25), "To savings", -200, db.TRANSFER)
            tx("Savings <Goal>", date(m.year, m.month, 25), "From checking", 200, db.TRANSFER)
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    for month in range(1, 13):
        key = f"{today.year}-{month:02d}"
        for name, amount in (("Wages & Tips", 4200), ("Mortgage / Rent", 1200), ("Groceries", 450)):
            conn.execute("INSERT INTO budgets (category_id, month, amount) VALUES (?, ?, ?)", (cat[name], key, amount))
    conn.execute("INSERT INTO settings (key, value) VALUES ('schema_version', '3')")
    conn.commit()
    conn.close()
    return path


if __name__ == "__main__":
    print(make(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "fake-budget.db")))
