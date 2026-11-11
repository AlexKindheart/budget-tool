"""Budget vs. actual numbers, modelled on the Report / YearlyReport / Weekly sheets.

"Actual" for an income category = deposits - payments; for an expense category = payments - deposits
(so refunds reduce spending). Non-budget categories ([Transfer], [Beginning Balance]) are left out.
"""
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


def actuals(conn, start, end):
    """{category_id: actual} for start <= date <= end (dates as date objects)."""
    rows = conn.execute(
        """SELECT c.id, c.kind, COALESCE(SUM(t.amount), 0) AS s FROM transactions t
           JOIN categories c ON c.id = t.category_id
           WHERE t.date BETWEEN ? AND ? AND c.kind != 'transfer' GROUP BY c.id""",
        (start.isoformat(), end.isoformat())).fetchall()
    return {r["id"]: (r["s"] if r["kind"] == "income" else -r["s"]) for r in rows}


def uncategorized(conn, start, end):
    """Money out minus money in with no category (counted as an expense)."""
    return -conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE category_id IS NULL AND date BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat())).fetchone()[0]


def budgets(conn, months):
    """{category_id: total budget across these 'YYYY-MM' months}."""
    if not months:
        return {}
    q = ",".join("?" * len(months))
    rows = conn.execute(
        f"SELECT category_id, SUM(amount) AS s FROM budgets WHERE month IN ({q}) GROUP BY category_id", months)
    return {r["category_id"]: r["s"] for r in rows}


def budget_vs_actual(conn, start, end, budget_scale=1.0, months=None):
    """Rows for a Report-style table. budget_scale prorates monthly budgets (weekly report)."""
    months = months or months_between(start, end)
    bud = budgets(conn, months)
    act = actuals(conn, start, end)
    sections = {"income": [], "expense": []}
    for c in categories(conn):
        if c["kind"] not in sections:
            continue
        b = round(bud.get(c["id"], 0) * budget_scale, 2)
        a = round(act.get(c["id"], 0), 2)
        if b or a:
            diff = a - b if c["kind"] == "income" else b - a  # positive = good
            sections[c["kind"]].append({"id": c["id"], "name": c["name"], "budget": b, "actual": a, "diff": diff})
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


def yearly(conn, year, start_balance=0.0):
    """Actual income/expenses per category for each month of the year (YearlyReport sheet)."""
    months = [date(year, m, 1) for m in range(1, 13)]
    per_month = [actuals(conn, m, add_months(m, 1) - timedelta(days=1)) for m in months]
    unc = [uncategorized(conn, m, add_months(m, 1) - timedelta(days=1)) for m in months]
    rows = {"income": [], "expense": []}
    for c in categories(conn):
        if c["kind"] not in rows:
            continue
        vals = [round(pm.get(c["id"], 0), 2) for pm in per_month]
        if any(vals):
            rows[c["kind"]].append({"id": c["id"], "name": c["name"], "values": vals, "total": sum(vals), "avg": sum(vals) / 12})
    if any(unc):
        vals = [round(v, 2) for v in unc]
        rows["expense"].append({"id": "none", "name": "Uncategorized", "values": vals, "total": sum(vals), "avg": sum(vals) / 12})
    inc = [sum(r["values"][i] for r in rows["income"]) for i in range(12)]
    exp = [sum(r["values"][i] for r in rows["expense"]) for i in range(12)]
    net = [inc[i] - exp[i] for i in range(12)]
    end, bal = [], start_balance
    for n in net:
        bal += n
        end.append(bal)
    return {"months": months, "income": rows["income"], "expense": rows["expense"],
            "inc": inc, "exp": exp, "net": net, "end": end}


def daily_balances(conn, months=None, today=None):
    """End-of-day register balance for each account, every day from the first of the month
    `months - 1` months ago through today (months=None: from the month of the earliest transaction,
    and at least a year back). Days before an account's first transaction are None.
    Credit cards and loans come out negative (money owed), so "all" is your net worth."""
    today = today or date.today()
    if months is None:
        start = add_months(date(today.year, today.month, 1), -12)
        earliest = conn.execute("SELECT MIN(date) FROM transactions").fetchone()[0]
        if earliest:
            e = date.fromisoformat(earliest[:10])
            start = min(start, date(e.year, e.month, 1))
    else:
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
