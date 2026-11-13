"""Bills and paychecks that repeat: what's coming up, and how much is safe to spend.

A bill has a name, an amount, how often it repeats (weekly, every two weeks, monthly, yearly) and the next date
it's due. Once that date has passed it moves on to the next one by itself: early if a matching payment shows up
in the register (same account and amount, within a few days of the due date), otherwise a few days after.

Safe to spend = what's in your everyday accounts (checking and cash) minus the bills due before your next
paycheck (or in the next 30 days if you haven't added a paycheck).
"""
import calendar
from datetime import date, timedelta

EVERY = [("week", "Every week"), ("2weeks", "Every 2 weeks"), ("month", "Every month"), ("year", "Every year")]
SPENDABLE = ("checking", "cash")
MATCH_DAYS = 5     # a payment this many days either side of the due date counts as paying it
GRACE_DAYS = 3     # with no payment found, move on this many days after the due date


def step(d, every, n=1):
    """The date n repeats after d (month ends are kept: Jan 31 -> Feb 28 -> Mar 31)."""
    if every == "week":
        return d + timedelta(days=7 * n)
    if every == "2weeks":
        return d + timedelta(days=14 * n)
    months = 12 * n if every == "year" else n
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _paid(conn, b, due):
    """A register entry that looks like this bill (or paycheck) around its due date."""
    if b["account_id"] is None:
        return False
    signed = b["amount"] if b["kind"] == "income" else -b["amount"]
    window = 2 if b["every"] == "week" else MATCH_DAYS  # weekly: don't reach into the week before or after
    return conn.execute(
        """SELECT 1 FROM transactions WHERE account_id = ? AND ABS(amount - ?) < 0.005 AND date BETWEEN ? AND ?
           LIMIT 1""",
        (b["account_id"], signed, (due - timedelta(days=window)).isoformat(),
         (due + timedelta(days=window)).isoformat())).fetchone() is not None


def refresh(conn, today=None):
    """Move bills whose due date is done (paid, or a few days past) on to their next date."""
    today = today or date.today()
    for b in conn.execute("SELECT * FROM bills").fetchall():
        due = date.fromisoformat(b["next_date"])
        moved = due
        for _ in range(400):  # never loop forever on bad data
            if moved > today + timedelta(days=MATCH_DAYS):
                break
            if (moved <= today and moved < today - timedelta(days=GRACE_DAYS - 1)) or _paid(conn, b, moved):
                moved = step(moved, b["every"])
            else:
                break
        if moved != due:
            conn.execute("UPDATE bills SET next_date = ? WHERE id = ?", (moved.isoformat(), b["id"]))


def upcoming(conn, days=30, today=None):
    """[{bill, date, amount}] due from today through today + days, soonest first (repeats included)."""
    today = today or date.today()
    end = today + timedelta(days=days)
    out = []
    for b in conn.execute(
            """SELECT b.*, a.name AS account, c.name AS category FROM bills b
               LEFT JOIN accounts a ON a.id = b.account_id LEFT JOIN categories c ON c.id = b.category_id"""):
        d = date.fromisoformat(b["next_date"])
        while d <= end:
            if d >= today:
                out.append({"bill": b, "date": d, "amount": b["amount"], "income": b["kind"] == "income"})
            d = step(d, b["every"])
    return sorted(out, key=lambda x: (x["date"], x["bill"]["name"].lower()))


def safe_to_spend(conn, balances, today=None):
    """{amount, cash, bills, until, paycheck}: everyday-account money minus bills before the next paycheck."""
    today = today or date.today()
    cash = sum(balances.get(a["id"], (0, 0))[0] for a in conn.execute(
        f"SELECT id FROM accounts WHERE closed = 0 AND kind IN ({','.join('?' * len(SPENDABLE))})", SPENDABLE))
    coming = upcoming(conn, 60, today)
    pay = next((x for x in coming if x["income"]), None)
    until = pay["date"] if pay else today + timedelta(days=30)
    due = [x for x in coming if not x["income"] and x["date"] < until]
    total = round(sum(x["amount"] for x in due), 2)
    return {"amount": round(cash - total, 2), "cash": round(cash, 2), "bills": total, "count": len(due),
            "until": until, "paycheck": pay}
