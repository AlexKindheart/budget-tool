"""Budget vs. actual numbers, modelled on the Report / YearlyReport / Weekly sheets.

"Actual" for an income category = deposits - payments; for an expense category = payments - deposits
(so refunds reduce spending). Non-budget categories ([Transfer], [Beginning Balance]) are left out.
Category totals come from the `lines` view: a split transaction counts as its parts (see db.py).
"""
import calendar
from datetime import date, timedelta


def month_key(d):
    return f"{d.year:04d}-{d.month:02d}"


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def months_between(first, last):
    """['YYYY-MM', ...] from the month of first to the month of last, inclusive."""
    out, d = [], date(first.year, first.month, 1)
    while d <= last:
        out.append(month_key(d))
        d = add_months(d, 1)
    return out


def categories(conn):
    return conn.execute(
        "SELECT * FROM categories ORDER BY CASE kind WHEN 'income' THEN 0 WHEN 'expense' THEN 1 ELSE 2 END, sort, name"
    ).fetchall()


def categories_in_year(conn, year):
    """Categories used in that year (a category can be limited to certain years, e.g. renamed for a new year)."""
    return [c for c in categories(conn)
            if (c["active_from"] is None or c["active_from"] <= year)
            and (c["active_until"] is None or c["active_until"] >= year)]


def actuals(conn, start, end):
    """{category_id: actual} for start <= date <= end (dates as date objects)."""
    rows = conn.execute(
        """SELECT c.id, c.kind, COALESCE(SUM(t.amount), 0) AS s FROM lines t
           JOIN categories c ON c.id = t.category_id
           WHERE t.date BETWEEN ? AND ? AND c.kind != 'transfer' GROUP BY c.id""",
        (start.isoformat(), end.isoformat())).fetchall()
    return {r["id"]: (r["s"] if r["kind"] == "income" else -r["s"]) for r in rows}


def uncategorized(conn, start, end):
    """Money out minus money in with no category (counted as an expense)."""
    return -conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM lines WHERE category_id IS NULL AND date BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat())).fetchone()[0]


def budgets(conn, months):
    """{category_id: total budget across these 'YYYY-MM' months}."""
    if not months:
        return {}
    q = ",".join("?" * len(months))
    rows = conn.execute(
        f"SELECT category_id, SUM(amount) AS s FROM budgets WHERE month IN ({q}) GROUP BY category_id", months)
    return {r["category_id"]: r["s"] for r in rows}


def prorated_budgets(conn, start, end):
    """{category_id: budget} for start..end. Each month's budget is spread evenly over its days, so a
    whole month (or year) gets its full budget and a week gets its share of each month it touches.
    Used everywhere (Overview, Reports, Export) so the same dates always show the same budget."""
    out, m = {}, date(start.year, start.month, 1)
    while m <= end:
        nxt = add_months(m, 1)
        last = nxt - timedelta(days=1)
        share = ((min(end, last) - max(start, m)).days + 1) / ((last - m).days + 1)
        for cid, amount in budgets(conn, [month_key(m)]).items():
            out[cid] = out.get(cid, 0) + amount * share
        m = nxt
    return out


def whole_month(start, end):
    """Is start..end exactly one calendar month?"""
    return start.day == 1 and end == add_months(start, 1) - timedelta(days=1)


def carried(conn, month_start):
    """{category_id: unspent budget brought forward} for categories set to carry over, into the month starting
    month_start. It builds up from January of that year (each year starts fresh) and can go negative when you
    overspent: next month has that much less."""
    cats = [r["id"] for r in conn.execute("SELECT id FROM categories WHERE rollover = 1 AND kind = 'expense'")]
    if not cats or month_start.month == 1:
        return {}
    out, m = {c: 0.0 for c in cats}, date(month_start.year, 1, 1)
    while m < month_start:
        last = add_months(m, 1) - timedelta(days=1)
        bud, act = budgets(conn, [month_key(m)]), actuals(conn, m, last)
        for c in cats:
            out[c] += bud.get(c, 0) - act.get(c, 0)
        m = add_months(m, 1)
    return {c: round(v, 2) for c, v in out.items() if round(v, 2)}


def budget_vs_actual(conn, start, end):
    """Rows for a Report-style table: budget (prorated by day for partial months) vs. actual. For a single month,
    categories set to carry over get last months' unspent budget added (row["carried"])."""
    bud = prorated_budgets(conn, start, end)
    act = actuals(conn, start, end)
    carry = carried(conn, start) if whole_month(start, end) else {}
    sections = {"income": [], "expense": []}
    for c in categories(conn):
        if c["kind"] not in sections:
            continue
        extra = carry.get(c["id"], 0)
        b = round(bud.get(c["id"], 0) + extra, 2)
        a = round(act.get(c["id"], 0), 2)
        if b or a:
            diff = a - b if c["kind"] == "income" else b - a  # positive = good
            sections[c["kind"]].append({"id": c["id"], "name": c["name"], "budget": b, "actual": a, "diff": diff,
                                        "carried": extra})
    unc = round(uncategorized(conn, start, end), 2)
    if unc:
        sections["expense"].append({"id": "none", "name": "Uncategorized", "budget": 0, "actual": unc, "diff": -unc})
    totals = {}
    for kind, rows in sections.items():
        tb, ta = sum(r["budget"] for r in rows), sum(r["actual"] for r in rows)
        totals[kind] = {"budget": tb, "actual": ta}
        for r in rows:
            r["pct_budget"] = r["budget"] / tb if tb else None
            r["pct_actual"] = r["actual"] / ta if ta else None
            r["used"] = r["actual"] / r["budget"] if r["budget"] else None
    totals["net"] = {"budget": totals["income"]["budget"] - totals["expense"]["budget"],
                     "actual": totals["income"]["actual"] - totals["expense"]["actual"]}
    return {"income": sections["income"], "expense": sections["expense"], "totals": totals}


def yearly(conn, year, start_balance=None, today=None):
    """Actual income/expenses per category for each month of the year (YearlyReport sheet).

    Balances come from the register, so End Balance always matches your accounts: start = all
    accounts at the end of last year; "other" = money that changed your balances without being income
    or spending (transfers to/from accounts that aren't in the app, opening balances); end = start +
    net + other, month by month. Months that haven't started yet have no end balance (None)."""
    today = today or date.today()
    if start_balance is None:
        start_balance = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE date < ?",
                                     (date(year, 1, 1).isoformat(),)).fetchone()[0]
    months = [date(year, m, 1) for m in range(1, 13)]
    # Averages cover the months that have started: all 12 for past years, Jan..this month for this year.
    span = 12 if year < today.year else (today.month if year == today.year else 0)
    per_month = [actuals(conn, m, add_months(m, 1) - timedelta(days=1)) for m in months]
    unc = [uncategorized(conn, m, add_months(m, 1) - timedelta(days=1)) for m in months]
    rows = {"income": [], "expense": []}
    for c in categories(conn):
        if c["kind"] not in rows:
            continue
        vals = [round(pm.get(c["id"], 0), 2) for pm in per_month]
        if any(vals):
            rows[c["kind"]].append({"id": c["id"], "name": c["name"], "values": vals, "total": sum(vals), "avg": sum(vals) / span if span else 0})
    if any(unc):
        vals = [round(v, 2) for v in unc]
        rows["expense"].append({"id": "none", "name": "Uncategorized", "values": vals, "total": sum(vals), "avg": sum(vals) / span if span else 0})
    inc = [sum(r["values"][i] for r in rows["income"]) for i in range(12)]
    exp = [sum(r["values"][i] for r in rows["expense"]) for i in range(12)]
    net = [inc[i] - exp[i] for i in range(12)]
    change = [conn.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE date BETWEEN ? AND ?",
                           (m.isoformat(), (add_months(m, 1) - timedelta(days=1)).isoformat())).fetchone()[0]
              for m in months]
    other = [round(change[i] - net[i], 2) for i in range(12)]
    end, bal = [], start_balance
    for i, m in enumerate(months):
        bal += change[i]
        end.append(round(bal, 2) if m <= today else None)
    return {"months": months, "income": rows["income"], "expense": rows["expense"],
            "inc": inc, "exp": exp, "net": net, "other": other, "end": end, "start": round(start_balance, 2), "span": span}


def daily_balances(conn, months=None, today=None, start=None, end=None):
    """End-of-day register balance for each account, every day from `start` through `end`
    (never past today, so a span still in progress stops at today and a future one is empty).
    Without start: the first of the month `months - 1` months ago, or (months=None) the month of the
    earliest transaction, at least a year back. Days before an account's first transaction are None.
    Credit cards and loans come out negative (money owed), so "all" is your net worth."""
    today = min(end, today or date.today()) if end else (today or date.today())
    if start is None and months is None:
        start = add_months(date(today.year, today.month, 1), -12)
        earliest = conn.execute("SELECT MIN(date) FROM transactions").fetchone()[0]
        if earliest:
            e = date.fromisoformat(earliest[:10])
            start = min(start, date(e.year, e.month, 1))
    elif start is None:
        start = add_months(date(today.year, today.month, 1), -(months - 1))
    dates = [start + timedelta(days=i) for i in range((today - start).days + 1)]
    accounts = conn.execute("SELECT id, name, kind, closed FROM accounts ORDER BY closed, sort, name").fetchall()
    series = []
    for slot, a in enumerate(accounts):
        before = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE account_id = ? AND date < ?",
                              (a["id"], start.isoformat())).fetchone()[0]
        first = conn.execute("SELECT MIN(date) FROM transactions WHERE account_id = ?", (a["id"],)).fetchone()[0]
        per_day = {r["date"]: r["s"] for r in conn.execute(
            "SELECT date, SUM(amount) AS s FROM transactions WHERE account_id = ? AND date BETWEEN ? AND ? GROUP BY date",
            (a["id"], start.isoformat(), today.isoformat()))}
        bal, values = before, []
        for d in dates:
            key = d.isoformat()
            bal += per_day.get(key, 0)
            values.append(round(bal, 2) if first and key >= first else None)
        series.append({"id": a["id"], "name": a["name"], "kind": a["kind"], "closed": bool(a["closed"]),
                       "slot": slot, "values": values})
    combined = []
    for i in range(len(dates)):
        vals = [s["values"][i] for s in series if s["values"][i] is not None]
        combined.append(round(sum(vals), 2) if vals else None)
    return {"dates": [d.isoformat() for d in dates], "accounts": series, "all": combined}


def budget_grid(conn, year):
    """{category_id: [12 monthly budget amounts]} plus summary rows (Budget sheet)."""
    months = [f"{year:04d}-{m:02d}" for m in range(1, 13)]
    grid = {}
    for r in conn.execute(
            "SELECT category_id, month, amount FROM budgets WHERE month LIKE ?", (f"{year:04d}-%",)):
        grid.setdefault(r["category_id"], [0.0] * 12)[months.index(r["month"])] = r["amount"]
    return months, grid


def budget_alerts(conn, today=None):
    """This month's expense categories that are over budget, or on pace to go over: [{name, id, budget, actual,
    left, days_left, pace, over}] most urgent first."""
    today = today or date.today()
    start = date(today.year, today.month, 1)
    end = add_months(start, 1) - timedelta(days=1)
    days = calendar.monthrange(today.year, today.month)[1]
    times = {r["category_id"]: r["n"] for r in conn.execute(
        "SELECT category_id, COUNT(*) AS n FROM lines WHERE date BETWEEN ? AND ? AND amount < 0 GROUP BY category_id",
        (start.isoformat(), today.isoformat()))}
    out = []
    for r in budget_vs_actual(conn, start, end)["expense"]:
        if not r["budget"] or r["budget"] <= 0 or r["id"] == "none":
            continue
        pace = r["actual"] / today.day * days if today.day else r["actual"]
        over = r["actual"] > r["budget"] + 0.005
        # "On pace to go over": a week into the month, at least half spent but not all of it, heading past the budget,
        # and spent a bit at a time (2+ payments). A once-a-month bill like rent isn't "on pace" for anything.
        heading = (today.day >= 7 and r["budget"] * 0.5 <= r["actual"] < r["budget"] - 0.005
                   and pace > r["budget"] * 1.05 and times.get(r["id"], 0) >= 2)
        if over or heading:
            out.append({"id": r["id"], "name": r["name"], "budget": r["budget"], "actual": r["actual"],
                        "left": round(r["budget"] - r["actual"], 2), "days_left": days - today.day,
                        "pace": round(pace, 2), "over": over})
    return sorted(out, key=lambda a: (not a["over"], a["left"]))


def payees(conn, start, end):
    """Spending by payee for start..end, biggest first: [{payee, spent, count, months, avg, repeats}].
    Spending = expense categories and Uncategorized (refunds reduce it). repeats: paid in 3 or more different
    months of the last 12 for about the same amount (within 10%), like a subscription or bill."""
    rows = conn.execute(
        """SELECT COALESCE(NULLIF(trim(l.payee), ''), '(no payee)') AS payee, -SUM(l.amount) AS spent,
                  COUNT(DISTINCT l.tx_id) AS n,
                  COUNT(DISTINCT substr(l.date, 1, 7)) AS months
           FROM lines l LEFT JOIN categories c ON c.id = l.category_id
           WHERE l.date BETWEEN ? AND ? AND l.is_opening = 0 AND (c.kind = 'expense' OR l.category_id IS NULL)
           GROUP BY lower(COALESCE(NULLIF(trim(l.payee), ''), '(no payee)'))
           HAVING -SUM(l.amount) > 0.005 ORDER BY spent DESC""",
        (start.isoformat(), end.isoformat())).fetchall()
    year_ago = (end - timedelta(days=365)).isoformat()
    out = []
    for r in rows:
        amounts = [-x[0] for x in conn.execute(
            """SELECT SUM(amount) FROM transactions WHERE lower(trim(payee)) = lower(?) AND amount < 0
               AND is_opening = 0 AND date BETWEEN ? AND ? GROUP BY substr(date, 1, 7)""",
            (r["payee"], year_ago, end.isoformat()))]
        typical = sorted(amounts)[len(amounts) // 2] if amounts else 0
        repeats = len(amounts) >= 3 and typical > 0 and all(abs(a - typical) <= typical * 0.10 for a in amounts)
        out.append({"payee": r["payee"], "spent": round(r["spent"], 2), "count": r["n"], "months": r["months"],
                    "avg": round(r["spent"] / r["n"], 2), "repeats": repeats, "typical": round(typical, 2)})
    return out
