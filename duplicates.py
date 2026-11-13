"""Bank transactions that duplicate one you typed in yourself.

You type in a check or a bill payment in an account that's linked to your bank; a few days later the bank sends the
same transaction. Without this the register has both and the money counts twice.

A bank transaction is combined with your entry when they're in the same account, for exactly the same amount, and
the bank's date is from 3 days before to 14 days after yours (checks can take a while to clear). Only posted bank
transactions are combined (pending ones can still change). The bank's copy stays (so later syncs keep updating it)
and takes your payee, memo and category; your entry is folded into it. "Not the same" splits them apart again and
they're never combined after that.
"""
import json
from datetime import date

DAYS_BEFORE = 3    # the bank's date can be this many days before yours
DAYS_AFTER = 14    # ...or this many days after (a check you wrote can take two weeks to clear)

_MINE = ("date", "payee", "memo", "category_id", "category_locked", "payee_locked")
_BANKS = ("payee", "memo", "category_id", "category_locked", "payee_locked")


def _cents(amount):
    return int(round(amount * 100))


def match(conn, account_id=None):
    """Combine duplicates (in one account, or every bank-linked account). Returns how many were combined."""
    where, params = "", []
    if account_id is not None:
        where, params = " AND t.account_id = ?", [account_id]
    mine = conn.execute(
        f"""SELECT t.* FROM transactions t JOIN accounts a ON a.id = t.account_id
            WHERE a.plaid_account_id IS NOT NULL AND t.plaid_id IS NULL AND t.is_opening = 0 AND t.no_match = 0
              AND t.amount != 0{where} ORDER BY t.date, t.id""", params).fetchall()
    if not mine:
        return 0
    bank = conn.execute(
        f"""SELECT t.* FROM transactions t WHERE t.plaid_id IS NOT NULL AND t.cleared != '' AND t.merged IS NULL
              AND t.no_match = 0 AND t.is_opening = 0{where}""", params).fetchall()
    by_amount = {}
    for b in bank:
        by_amount.setdefault((b["account_id"], _cents(b["amount"])), []).append(b)
    used, count = set(), 0
    for m in mine:
        best = None
        for b in by_amount.get((m["account_id"], _cents(m["amount"])), []):
            if b["id"] in used:
                continue
            gap = (date.fromisoformat(b["date"][:10]) - date.fromisoformat(m["date"][:10])).days
            if -DAYS_BEFORE <= gap <= DAYS_AFTER and (best is None or abs(gap) < best[0]):
                best = (abs(gap), b)
        if best:
            used.add(best[1]["id"])
            combine(conn, m, best[1])
            count += 1
    return count


def _parts(conn, tx_id):
    return [[r["category_id"], r["amount"], r["memo"]] for r in conn.execute(
        "SELECT category_id, amount, memo FROM splits WHERE tx_id = ? ORDER BY id", (tx_id,))]


def _set_parts(conn, tx_id, parts):
    conn.execute("DELETE FROM splits WHERE tx_id = ?", (tx_id,))
    conn.executemany("INSERT INTO splits (tx_id, category_id, amount, memo) VALUES (?, ?, ?, ?)",
                     [(tx_id, c, a, m) for c, a, m in parts])


def combine(conn, mine, bank):
    """Fold your entry into the bank's copy: your payee, memo and category (or split) win where you filled them
    in."""
    saved = {"mine": {k: mine[k] for k in _MINE}, "bank": {k: bank[k] for k in _BANKS},
             "mine_parts": _parts(conn, mine["id"]), "bank_parts": _parts(conn, bank["id"])}
    if saved["mine_parts"]:  # your split wins
        _set_parts(conn, bank["id"], saved["mine_parts"])
    conn.execute(
        """UPDATE transactions SET payee = ?, payee_locked = ?, memo = ?, category_id = ?, category_locked = ?,
           merged = ? WHERE id = ?""",
        (mine["payee"] or bank["payee"], 1 if mine["payee"] else bank["payee_locked"],
         mine["memo"] or bank["memo"],
         mine["category_id"] if mine["category_id"] is not None else bank["category_id"],
         1 if mine["category_id"] is not None else bank["category_locked"],
         json.dumps(saved), bank["id"]))
    if saved["mine_parts"]:
        conn.execute("UPDATE transactions SET category_id = NULL, category_locked = 1 WHERE id = ?", (bank["id"],))
    conn.execute("DELETE FROM transactions WHERE id = ?", (mine["id"],))


def _put_back_mine(conn, row, saved, no_match):
    m = saved["mine"]
    new_id = conn.execute(
        """INSERT INTO transactions (account_id, date, payee, memo, amount, category_id, cleared,
                                     category_locked, payee_locked, no_match) VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?)""",
        (row["account_id"], m["date"], m["payee"], m["memo"], row["amount"], m["category_id"],
         m["category_locked"], m["payee_locked"], no_match)).lastrowid
    if saved.get("mine_parts"):
        _set_parts(conn, new_id, saved["mine_parts"])


def split(conn, tx_id):
    """"Not the same": your entry comes back as it was, the bank's copy goes back to how the bank sent it, and the
    two are never combined again. Returns False if it wasn't combined."""
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if not row or not row["merged"]:
        return False
    saved = json.loads(row["merged"])
    _put_back_mine(conn, row, saved, 1)
    b = saved["bank"]
    if saved.get("mine_parts") or saved.get("bank_parts"):
        _set_parts(conn, tx_id, saved.get("bank_parts") or [])
    conn.execute(
        """UPDATE transactions SET payee = ?, memo = ?, category_id = ?, category_locked = ?, payee_locked = ?,
           merged = NULL, no_match = 1 WHERE id = ?""",
        (b["payee"], b["memo"], b["category_id"], b["category_locked"], b["payee_locked"], tx_id))
    return True


def bank_removed(conn, plaid_ids):
    """The bank withdrew some transactions. Any of your entries combined into them come back on their own."""
    for pid in plaid_ids:
        row = conn.execute("SELECT * FROM transactions WHERE plaid_id = ? AND merged IS NOT NULL", (pid,)).fetchone()
        if row:
            _put_back_mine(conn, row, json.loads(row["merged"]), 0)


def describe(row):
    """'You typed this in on 2026-03-01 as "Rent check"' for the Register's label."""
    try:
        m = json.loads(row["merged"])["mine"]
    except (TypeError, ValueError, KeyError):
        return ""
    return f"You typed this in on {m['date']}" + (f" as \"{m['payee']}\"" if m.get("payee") else "") + \
        ". Your bank sent it too, so the two were combined and it only counts once."
