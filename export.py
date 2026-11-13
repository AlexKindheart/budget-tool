"""Excel export (Export tab): one workbook for any date range and any set of accounts.

Sheets: Summary (budget vs. actual by category), Transactions (an Excel table you can sort and filter),
By month (each category per month), Accounts (starting balance, money in / out, ending balance).
Same rules as the Reports screen: transfers and opening balances aren't income or spending, and
uncategorized money counts as spending. Budgets for part of a month are prorated by day.
"""
import io
from datetime import date, timedelta

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

import reports

SHEETS = [
    ("summary", "Summary", "Budget vs. actual for each category"),
    ("transactions", "Transactions", "Every transaction, ready to sort and filter"),
    ("monthly", "By month", "Each category's total for every month"),
    ("accounts", "Accounts", "Starting balance, money in and out, ending balance"),
]
SHEET_KEYS = [k for k, _, _ in SHEETS]

MONEY = '"$"#,##0.00;[Red]-"$"#,##0.00'
DATE = "yyyy-mm-dd"
TITLE = Font(bold=True, size=14)
BOLD = Font(bold=True)
MUTED = Font(italic=True, color="666666")
HEAD = PatternFill("solid", fgColor="DCE6F5")
BAND = PatternFill("solid", fgColor="F2F4F7")


def nice_date(d):
    return f"{d:%b} {d.day}, {d.year}"


def _in_accounts(ids):
    return "t.account_id IN (" + ",".join("?" * len(ids)) + ")", list(ids)


def preview(conn, start, end, ids):
    """Count and money in / out for the chosen dates and accounts (shown before saving)."""
    if not ids:
        return {"count": 0, "money_in": 0.0, "money_out": 0.0}
    where, params = _in_accounts(ids)
    r = conn.execute(
        f"""SELECT COUNT(*) AS n,
                   COALESCE(SUM(CASE WHEN t.amount > 0 THEN t.amount END), 0) AS i,
                   COALESCE(-SUM(CASE WHEN t.amount < 0 THEN t.amount END), 0) AS o
            FROM transactions t WHERE {where} AND t.date BETWEEN ? AND ?""",
        params + [start.isoformat(), end.isoformat()]).fetchone()
    return {"count": r["n"], "money_in": round(r["i"], 2), "money_out": round(r["o"], 2)}


def _sums(conn, start, end, ids, by_month=False):
    """Raw register sums per category (None = uncategorized), optionally per 'YYYY-MM'."""
    where, params = _in_accounts(ids)
    month = "substr(t.date, 1, 7)" if by_month else "''"
    rows = conn.execute(
        f"""SELECT t.category_id AS cid, {month} AS m, SUM(t.amount) AS s FROM lines t
            WHERE {where} AND t.date BETWEEN ? AND ? GROUP BY t.category_id, {month}""",
        params + [start.isoformat(), end.isoformat()])
    return {(r["cid"], r["m"]) if by_month else r["cid"]: r["s"] for r in rows}


def _header(ws, row, labels):
    for col, text in enumerate(labels, 1):
        c = ws.cell(row=row, column=col, value=text)
        c.font, c.fill = BOLD, HEAD
        c.alignment = Alignment(horizontal="left" if col == 1 else "right")


def _widths(ws, widths):
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = w


def _intro(ws, title, start, end, account_label, note=None):
    ws["A1"] = title
    ws["A1"].font = TITLE
    ws["A2"] = f"{nice_date(start)} – {nice_date(end)}  ·  {account_label}"
    if note:
        ws["A3"] = note
        ws["A3"].font = MUTED


def _summary(wb, conn, start, end, ids, account_label, cats):
    ws = wb.create_sheet("Summary")
    _intro(ws, "Budget vs. actual", start, end, account_label,
           "Budgets for part of a month are prorated by day. Transfers and opening balances aren't counted."
           + (" Budgets cover all your accounts." if account_label != "All accounts" else ""))
    bud, act = reports.prorated_budgets(conn, start, end), _sums(conn, start, end, ids)
    row = 5
    _header(ws, row, ["Category", "Budget", "Actual", "Difference"])
    totals = {}
    for kind, label in (("income", "Income"), ("expense", "Expenses")):
        row += 2
        ws.cell(row=row, column=1, value=label).font = BOLD
        first = row + 1
        lines = []
        for c in cats:
            if c["kind"] != kind:
                continue
            b = round(bud.get(c["id"], 0), 2)
            a = round(act.get(c["id"], 0) * (1 if kind == "income" else -1), 2)
            if b or a:
                lines.append((c["name"], b, a))
        if kind == "expense" and act.get(None):
            lines.append(("Uncategorized", 0, round(-act[None], 2)))
        for name, b, a in lines:
            row += 1
            ws.cell(row=row, column=1, value=name)
            ws.cell(row=row, column=2, value=b)
            ws.cell(row=row, column=3, value=a)
            ws.cell(row=row, column=4, value=f"=C{row}-B{row}" if kind == "income" else f"=B{row}-C{row}")
        row += 1
        ws.cell(row=row, column=1, value=f"Total {label.lower()}").font = BOLD
        for col in (2, 3, 4):
            letter = get_column_letter(col)
            c = ws.cell(row=row, column=col, value=f"=SUM({letter}{first}:{letter}{row - 1})" if lines else 0)
            c.font = BOLD
        totals[kind] = row
    row += 2
    ws.cell(row=row, column=1, value="Net (income - expenses)").font = BOLD
    i, e = totals["income"], totals["expense"]
    for col, f in ((2, f"=B{i}-B{e}"), (3, f"=C{i}-C{e}"), (4, f"=C{row}-B{row}")):
        ws.cell(row=row, column=col, value=f).font = BOLD
    for r in ws.iter_rows(min_row=6, max_row=row, min_col=2, max_col=4):
        for c in r:
            c.number_format = MONEY
    _widths(ws, [34, 15, 15, 15])
    ws.freeze_panes = "A6"


def _transactions(wb, conn, start, end, ids, account_label):
    ws = wb.create_sheet("Transactions")
    where, params = _in_accounts(ids)
    rows = conn.execute(
        f"""SELECT t.date, a.name AS account, t.payee, c.name AS category, l.memo, t.bank_name, l.amount
            FROM lines l JOIN transactions t ON t.id = l.tx_id JOIN accounts a ON a.id = t.account_id
            LEFT JOIN categories c ON c.id = l.category_id
            WHERE {where} AND t.date BETWEEN ? AND ? ORDER BY t.date, t.id, l.split_id""",
        params + [start.isoformat(), end.isoformat()]).fetchall()
    _header(ws, 1, ["Date", "Account", "Payee", "Category", "Memo", "Bank description", "Payment", "Deposit", "Amount"])
    for r in rows:
        amt = r["amount"]
        ws.append([date.fromisoformat(r["date"][:10]), r["account"], r["payee"] or "", r["category"] or "Uncategorized",
                   r["memo"] or "", r["bank_name"] or "", -amt if amt < 0 else None, amt if amt > 0 else None, amt])
    if rows:
        last = len(rows) + 1
        table = Table(displayName="TransactionList", ref=f"A1:I{last}")
        table.tableStyleInfo = TableStyleInfo(name="TableStyleLight9", showRowStripes=True)
        ws.add_table(table)
        for (cell,) in ws.iter_rows(min_row=2, max_row=last, min_col=1, max_col=1):
            cell.number_format = DATE
        for r in ws.iter_rows(min_row=2, max_row=last, min_col=7, max_col=9):
            for c in r:
                c.number_format = MONEY
    else:
        ws["A2"] = "No transactions in this date range."
        ws["A2"].font = MUTED
    _widths(ws, [12, 20, 28, 22, 24, 34, 13, 13, 13])
    ws.freeze_panes = "A2"


def _monthly(wb, conn, start, end, ids, account_label, cats):
    ws = wb.create_sheet("By month")
    months = reports.months_between(start, end)
    partial = start.day != 1 or (end + timedelta(days=1)).day != 1
    _intro(ws, "Each category by month", start, end, account_label,
           "The first and last months only include the days in your date range." if partial else None)
    sums = _sums(conn, start, end, ids, by_month=True)
    n = len(months)
    total_col, avg_col = n + 2, n + 3
    tl, fl, ll = get_column_letter(total_col), get_column_letter(2), get_column_letter(n + 1)
    row = 5
    _header(ws, row, ["Category"] + [date.fromisoformat(m + "-01").strftime("%b %Y") for m in months] + ["Total", "Average"])
    totals = {}
    for kind, label in (("income", "Income"), ("expense", "Expenses")):
        row += 2
        ws.cell(row=row, column=1, value=label).font = BOLD
        first = row + 1
        lines = [(c["name"], c["id"]) for c in cats if c["kind"] == kind]
        if kind == "expense":
            lines.append(("Uncategorized", None))
        sign = 1 if kind == "income" else -1
        for name, cid in lines:
            vals = [round(sums.get((cid, m), 0) * sign, 2) for m in months]
            if not any(vals):
                continue
            row += 1
            ws.cell(row=row, column=1, value=name)
            for i, v in enumerate(vals):
                ws.cell(row=row, column=i + 2, value=v)
            ws.cell(row=row, column=total_col, value=f"=SUM({fl}{row}:{ll}{row})")
            ws.cell(row=row, column=avg_col, value=f"={tl}{row}/{n}")
        row += 1
        ws.cell(row=row, column=1, value=f"Total {label.lower()}").font = BOLD
        for col in range(2, avg_col + 1):
            letter = get_column_letter(col)
            ws.cell(row=row, column=col, value=f"=SUM({letter}{first}:{letter}{row - 1})" if row > first else 0).font = BOLD
        totals[kind] = row
    row += 2
    ws.cell(row=row, column=1, value="Net (income - expenses)").font = BOLD
    for col in range(2, avg_col + 1):
        letter = get_column_letter(col)
        ws.cell(row=row, column=col, value=f"={letter}{totals['income']}-{letter}{totals['expense']}").font = BOLD
    for r in ws.iter_rows(min_row=6, max_row=row, min_col=2, max_col=avg_col):
        for c in r:
            c.number_format = MONEY
    _widths(ws, [30] + [12] * n + [14, 13])
    ws.freeze_panes = "B6"


def _accounts(wb, conn, start, end, ids, account_label):
    ws = wb.create_sheet("Accounts")
    _intro(ws, "Account balances", start, end, account_label,
           "Credit cards and loans show as negative balances (money owed).")
    before = start - timedelta(days=1)
    _header(ws, 5, ["Account", "Type", f"Balance {nice_date(before)}", "Money in", "Money out", f"Balance {nice_date(end)}"])
    row = 5
    for a in conn.execute(f"SELECT id, name, kind, closed FROM accounts WHERE id IN ({','.join('?' * len(ids))}) "
                          "ORDER BY closed, sort, name", ids).fetchall():
        opening = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE account_id = ? AND date < ?",
                               (a["id"], start.isoformat())).fetchone()[0]
        flows = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN amount > 0 THEN amount END), 0),
                      COALESCE(-SUM(CASE WHEN amount < 0 THEN amount END), 0)
               FROM transactions WHERE account_id = ? AND date BETWEEN ? AND ?""",
            (a["id"], start.isoformat(), end.isoformat())).fetchone()
        row += 1
        ws.append([a["name"] + (" (closed)" if a["closed"] else ""), (a["kind"] or "").title(),
                   round(opening, 2), round(flows[0], 2), round(flows[1], 2), f"=C{row}+D{row}-E{row}"])
    row += 1
    ws.cell(row=row, column=1, value="Total").font = BOLD
    for col in range(3, 7):
        letter = get_column_letter(col)
        ws.cell(row=row, column=col, value=f"=SUM({letter}6:{letter}{row - 1})" if row > 6 else 0).font = BOLD
    for r in ws.iter_rows(min_row=6, max_row=row, min_col=3, max_col=6):
        for c in r:
            c.number_format = MONEY
    _widths(ws, [30, 12, 22, 14, 14, 22])
    ws.freeze_panes = "A6"


def build(conn, start, end, ids, sheets):
    """The .xlsx file as bytes."""
    names = [r["name"] for r in conn.execute(
        f"SELECT name FROM accounts WHERE id IN ({','.join('?' * len(ids))}) ORDER BY closed, sort, name", ids)]
    total = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    account_label = "All accounts" if len(names) == total else ", ".join(names)
    cats = reports.categories(conn)
    wb = Workbook()
    wb.remove(wb.active)
    wb.properties.creator = "Budget Tool"
    wb.properties.title = f"Budget {start} to {end}"
    if "summary" in sheets:
        _summary(wb, conn, start, end, ids, account_label, cats)
    if "transactions" in sheets:
        _transactions(wb, conn, start, end, ids, account_label)
    if "monthly" in sheets:
        _monthly(wb, conn, start, end, ids, account_label, cats)
    if "accounts" in sheets:
        _accounts(wb, conn, start, end, ids, account_label)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
