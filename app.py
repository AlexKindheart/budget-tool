"""Budget Tool screens and actions (Flask). Shown inside the desktop window by budget_tool.py.

The server only listens on 127.0.0.1 and every request needs the per-launch key
(LAUNCH_KEY), so other programs and websites can't read your data.
"""
import io
import os
import secrets
import subprocess
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta, timezone

import webview
from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

import db
import export
import plaid_client as plaid
import reports
import updater
from paths import DATA_DIR, RESOURCE_DIR

db.init()

app = Flask(__name__,
            template_folder=os.path.join(RESOURCE_DIR, "templates"),
            static_folder=os.path.join(RESOURCE_DIR, "static"))
app.secret_key = secrets.token_hex(32)  # new each launch; sessions only live while the app is open
app.config["SESSION_COOKIE_SAMESITE"] = "Strict"

LAUNCH_KEY = secrets.token_urlsafe(24)
BASE_URL = ""          # set by start_server()
DATA_VERSION = [0]     # bumped when a bank is (re)connected, so the window knows to refresh
WINDOW_MESSAGES = []   # messages from the browser sign-in, shown in the app window next time it loads
ACCOUNT_KINDS = ["checking", "savings", "credit", "loan", "cash", "other"]


# ---------------------------------------------------------------- request plumbing

def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.before_request
def check_access():
    # Per-launch key: given once in the URL, then remembered in the session cookie.
    if not session.get("ok"):
        if request.args.get("key") and secrets.compare_digest(request.args["key"], LAUNCH_KEY):
            session["ok"] = True
        elif request.endpoint != "static":
            abort(403)
    if request.method == "POST":
        sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token")
        if not sent or sent != session.get("csrf"):
            abort(400, "Form expired - go back, refresh the page and try again.")


@app.before_request
def deliver_window_messages():
    if request.method == "GET" and request.args.get("browser") != "1" and not request.path.startswith(("/api/", "/static/")):
        while WINDOW_MESSAGES:
            flash(*WINDOW_MESSAGES.pop(0))


@app.context_processor
def inject_globals():
    return {"csrf_token": csrf_token, "plaid_env": plaid.env(), "plaid_ready": plaid.configured(),
            "today": date.today(), "update": updater.status()}


@app.template_filter("money")
def money(value):
    value = value or 0
    sign = "-" if value < -0.004 else ""
    return f"{sign}${abs(value):,.2f}"


@app.template_filter("pct")
def pct(value):
    return "-" if value is None else f"{value * 100:.0f}%"


@app.template_filter("ago")
def ago(iso):
    if not iso:
        return "never"
    secs = (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)} hr ago"
    return f"{int(secs // 86400)} days ago"


@app.template_filter("mon")
def mon(value):
    """'2026-01' or a date -> 'Jan'."""
    d = value if isinstance(value, date) else datetime.strptime(value, "%Y-%m").date()
    return d.strftime("%b")


def parse_month(raw, default=None):
    try:
        return datetime.strptime(raw or "", "%Y-%m").date()
    except ValueError:
        return default or date.today().replace(day=1)


def parse_money(raw):
    raw = (raw or "").replace(",", "").replace("$", "").strip()
    if not raw:
        return None
    try:
        return round(float(raw), 2)
    except ValueError:
        return None


def back(default="overview"):
    return redirect(request.form.get("next") or request.referrer or url_for(default))


def all_categories(conn):
    return reports.categories(conn)


def all_accounts(conn, include_closed=True):
    return conn.execute(
        "SELECT * FROM accounts" + ("" if include_closed else " WHERE closed = 0") + " ORDER BY closed, sort, name"
    ).fetchall()


def account_balances(conn):
    """{account_id: (balance, cleared_balance)} from the register."""
    out = {}
    for r in conn.execute(
            "SELECT account_id, SUM(amount) AS bal, SUM(CASE WHEN cleared != '' THEN amount ELSE 0 END) AS clr "
            "FROM transactions GROUP BY account_id"):
        out[r["account_id"]] = (r["bal"] or 0, r["clr"] or 0)
    return out


def last_synced(conn):
    return conn.execute("SELECT MAX(last_synced) FROM items").fetchone()[0]


def run_sync_and_flash():
    if not plaid.configured():
        flash("Add your Plaid keys on the Settings screen first.", "error")
        return
    totals, errors = plaid.sync_all()
    flash(f"Synced: {totals['added']} new, {totals['modified']} updated, {totals['removed']} removed.", "ok")
    for err in errors:
        flash(err, "error")


# ---------------------------------------------------------------- overview

def week_start(d):
    """Sunday on or before d (weeks run Sunday - Saturday, like the weekly report)."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def week_label(start):
    end = start + timedelta(days=6)
    if start.year != end.year:
        return f"{start:%b} {start.day}, {start.year} – {end:%b} {end.day}, {end.year}"
    if start.month != end.month:
        return f"{start:%b} {start.day} – {end:%b} {end.day}, {end.year}"
    return f"{start:%b} {start.day} – {end.day}, {end.year}"


def overview_period(args, earliest):
    """The time span the Overview summarizes: all time, a year, a month (default) or a week.
    Year / month / week default to the current one. `choices` fills the dropdown, newest first."""
    today = date.today()
    earliest = earliest or today
    kind = args.get("period", "month")
    p = {"period": kind, "scale": 1.0, "months": None, "prev": None, "next": None, "choices": [], "selected": ""}
    if kind == "all":
        p.update(start=min(earliest, today), end=today, label="All Time")
    elif kind == "year":
        try:
            y = min(max(int(args.get("year", "")), 1900), 9998)
        except ValueError:
            y = today.year
        p.update(start=date(y, 1, 1), end=date(y, 12, 31), label=str(y), selected=str(y),
                 prev=url_for("overview", period="year", year=y - 1), next=url_for("overview", period="year", year=y + 1))
        years = range(max(today.year, y), min(earliest.year, y) - 1, -1)
        p["choices"] = [(str(v), str(v)) for v in years]
    elif kind == "week":
        try:
            s = week_start(date.fromisoformat(args.get("week", "")))
        except ValueError:
            s = week_start(today)
        prev_w, next_w = s - timedelta(days=7), s + timedelta(days=7)
        p.update(start=s, end=s + timedelta(days=6), label=week_label(s), selected=s.isoformat(),
                 scale=12 / 52, months=[reports.month_key(s)],
                 prev=url_for("overview", period="week", week=prev_w.isoformat()),
                 next=url_for("overview", period="week", week=next_w.isoformat()))
        w, stop = max(week_start(today), s), min(week_start(earliest), s)
        while w >= stop:
            p["choices"].append((w.isoformat(), week_label(w)))
            w -= timedelta(days=7)
    else:
        first = parse_month(args.get("month"))
        p.update(period="month", start=first, end=reports.add_months(first, 1) - timedelta(days=1),
                 label=first.strftime("%B %Y"), selected=reports.month_key(first),
                 prev=url_for("overview", period="month", month=reports.month_key(reports.add_months(first, -1))),
                 next=url_for("overview", period="month", month=reports.month_key(reports.add_months(first, 1))))
        m, stop = max(today.replace(day=1), first), min(earliest.replace(day=1), first)
        while m >= stop:
            p["choices"].append((reports.month_key(m), m.strftime("%B %Y")))
            m = reports.add_months(m, -1)
    return p


@app.route("/")
def overview():
    with db.connect() as conn:
        earliest = conn.execute("SELECT MIN(date) FROM transactions").fetchone()[0]
        p = overview_period(request.args, date.fromisoformat(earliest[:10]) if earliest else None)
        rep = reports.budget_vs_actual(conn, p["start"], p["end"], budget_scale=p["scale"], months=p["months"])
        trend = reports.daily_balances(conn)
        accounts = all_accounts(conn, include_closed=False)
        balances = account_balances(conn)
        recent = conn.execute(
            """SELECT t.*, c.name AS category, a.name AS account FROM transactions t
               LEFT JOIN categories c ON c.id = t.category_id JOIN accounts a ON a.id = t.account_id
               WHERE t.is_opening = 0 ORDER BY t.date DESC, t.id DESC LIMIT 10""").fetchall()
        synced = last_synced(conn)
        has_items = conn.execute("SELECT 1 FROM items").fetchone() is not None
    return render_template(
        "overview.html", p=p, month=reports.month_key(p["start"] if p["period"] == "month" else date.today()),
        rep=rep, trend=trend, accounts=accounts, balances=balances, recent=recent, last_synced=synced,
        has_items=has_items)


# ---------------------------------------------------------------- register (Transactions sheet)

@app.route("/register")
def register():
    month_raw = request.args.get("month", reports.month_key(date.today()))
    account = request.args.get("account", "")
    cat = request.args.get("category", "")
    q = request.args.get("q", "").strip()
    uncleared = request.args.get("uncleared") == "1"

    where, params = ["1=1"], []
    # month=range&from=YYYY-MM-DD&to=YYYY-MM-DD: a date span (links from the Overview's year / week / all time).
    span = None
    if month_raw == "range":
        try:
            span = (date.fromisoformat(request.args.get("from", "")), date.fromisoformat(request.args.get("to", "")))
        except ValueError:
            month_raw = reports.month_key(date.today())
    if span:
        where.append("t.date BETWEEN ? AND ?")
        params += [span[0].isoformat(), span[1].isoformat()]
    elif month_raw != "all":
        first = parse_month(month_raw)
        month_raw = reports.month_key(first)
        where.append("substr(t.date, 1, 7) = ?")
        params.append(month_raw)
    if account.isdigit():
        where.append("t.account_id = ?")
        params.append(int(account))
    if cat == "none":
        where.append("t.category_id IS NULL")
    elif cat.isdigit():
        where.append("t.category_id = ?")
        params.append(int(cat))
    if q:
        like = "%" + q.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        where.append("(t.payee LIKE ? ESCAPE '!' OR t.memo LIKE ? ESCAPE '!' OR t.bank_name LIKE ? ESCAPE '!')")
        params += [like, like, like]
    if uncleared:
        where.append("t.cleared = ''")

    with db.connect() as conn:
        txs = conn.execute(
            f"""SELECT t.*, c.name AS category, c.kind AS category_kind, a.name AS account
                FROM transactions t JOIN accounts a ON a.id = t.account_id
                LEFT JOIN categories c ON c.id = t.category_id
                WHERE {' AND '.join(where)} ORDER BY t.date DESC, t.id DESC LIMIT 3000""", params).fetchall()
        # Running balance per account, like the Account Balance column.
        running, totals = {}, {}
        for r in conn.execute("SELECT id, account_id, amount FROM transactions ORDER BY date, id"):
            totals[r["account_id"]] = totals.get(r["account_id"], 0) + r["amount"]
            running[r["id"]] = totals[r["account_id"]]
        categories = all_categories(conn)
        accounts = all_accounts(conn)
    month_d = parse_month(month_raw) if month_raw not in ("all", "range") else date.today().replace(day=1)
    if span:
        label = f"{span[0]:%b} {span[0].day}, {span[0].year} – {span[1]:%b} {span[1].day}, {span[1].year}"
    else:
        label = "All dates" if month_raw == "all" else month_d.strftime("%B %Y")
    return render_template(
        "register.html", txs=txs, running=running, categories=categories, accounts=accounts,
        month=month_raw, label=label, span=span,
        prev=reports.month_key(reports.add_months(month_d, -1)), nxt=reports.month_key(reports.add_months(month_d, 1)),
        account=account, cat=cat, q=q, uncleared=uncleared,
        total_in=sum(t["amount"] for t in txs if t["amount"] > 0),
        total_out=-sum(t["amount"] for t in txs if t["amount"] < 0))


def _tx_from_form(conn):
    acc = request.form.get("account_id", "")
    d = request.form.get("date", "")
    pay, dep = parse_money(request.form.get("payment")), parse_money(request.form.get("deposit"))
    if not acc.isdigit() or not conn.execute("SELECT 1 FROM accounts WHERE id = ?", (int(acc),)).fetchone():
        raise ValueError("Pick an account.")
    try:
        d = date.fromisoformat(d).isoformat()
    except ValueError:
        raise ValueError("Enter a valid date.")
    if pay is None and dep is None:
        raise ValueError("Enter a payment or a deposit amount.")
    cat = request.form.get("category_id", "")
    cleared = request.form.get("cleared", "")
    return {
        "account_id": int(acc), "date": d, "payee": request.form.get("payee", "").strip(),
        "memo": request.form.get("memo", "").strip(), "amount": round((dep or 0) - (pay or 0), 2),
        "category_id": int(cat) if cat.isdigit() else None,
        "cleared": cleared if cleared in ("", "c", "R") else "",
    }


@app.post("/register/add")
def register_add():
    with db.connect() as conn:
        try:
            t = _tx_from_form(conn)
        except ValueError as e:
            flash(str(e), "error")
            return back("register")
        conn.execute(
            """INSERT INTO transactions (account_id, date, payee, memo, amount, category_id, cleared,
                                         category_locked, payee_locked) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)""",
            (t["account_id"], t["date"], t["payee"], t["memo"], t["amount"], t["category_id"], t["cleared"]))
        plaid.refresh_opening_balance(conn, t["account_id"])
    flash("Transaction added.", "ok")
    return back("register")


@app.route("/register/<int:tx_id>", methods=["GET", "POST"])
def register_edit(tx_id):
    with db.connect() as conn:
        tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if not tx:
            abort(404)
        if request.method == "POST":
            try:
                t = _tx_from_form(conn)
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("register_edit", tx_id=tx_id, next=request.form.get("next")))
            if tx["plaid_id"]:  # the bank owns date/amount/account; you own payee/memo/category/cleared
                t.update(account_id=tx["account_id"], date=tx["date"], amount=tx["amount"])
            conn.execute(
                """UPDATE transactions SET account_id=?, date=?, payee=?, memo=?, amount=?, category_id=?,
                   cleared=?, category_locked=1, payee_locked=1 WHERE id=?""",
                (t["account_id"], t["date"], t["payee"], t["memo"], t["amount"], t["category_id"], t["cleared"], tx_id))
            match = request.form.get("rule_match", "").strip().lower()
            if request.form.get("make_rule") and match:
                conn.execute(
                    "INSERT INTO rules (match, payee, category_id) VALUES (?, ?, ?) "
                    "ON CONFLICT(match) DO UPDATE SET payee = excluded.payee, category_id = excluded.category_id",
                    (match, t["payee"] or None, t["category_id"]))
                n = db.reapply_rules(conn)
                flash(f"Saved, and made a rule for \"{match}\" ({n} other bank transactions updated).", "ok")
            else:
                flash("Saved.", "ok")
            for acc_id in {tx["account_id"], t["account_id"]}:
                plaid.refresh_opening_balance(conn, acc_id)
            return redirect(request.form.get("next") or url_for("register"))
        categories = all_categories(conn)
        accounts = all_accounts(conn)
    return render_template("tx_edit.html", tx=tx, categories=categories, accounts=accounts,
                           next=request.args.get("next") or url_for("register"))


@app.post("/register/<int:tx_id>/delete")
def register_delete(tx_id):
    with db.connect() as conn:
        tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if tx and tx["plaid_id"]:
            flash("Bank transactions can't be deleted (the next sync would bring them back). "
                  "Give it the [Transfer] category to leave it out of your budget.", "error")
        elif tx:
            conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
            plaid.refresh_opening_balance(conn, tx["account_id"])
            flash("Transaction deleted.", "ok")
    return redirect(request.form.get("next") or url_for("register"))


@app.post("/register/<int:tx_id>/category")
def register_category(tx_id):
    cat_raw = request.form.get("category_id", "")
    category_id = int(cat_raw) if cat_raw.isdigit() else None
    with db.connect() as conn:
        tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if not tx:
            abort(404)
        conn.execute("UPDATE transactions SET category_id = ?, category_locked = 1 WHERE id = ?", (category_id, tx_id))
        if request.form.get("apply_rule") and category_id:
            match = (tx["payee"] or tx["bank_name"] or "").strip().lower()
            if match:
                conn.execute(
                    "INSERT INTO rules (match, category_id) VALUES (?, ?) "
                    "ON CONFLICT(match) DO UPDATE SET category_id = excluded.category_id", (match, category_id))
                n = db.reapply_rules(conn)
                flash(f"Rule saved: \"{match}\" is always this category ({n} other bank transactions updated).", "ok")
    return back("register")


@app.post("/register/<int:tx_id>/cleared")
def register_cleared(tx_id):
    """Cycle the Clr column: blank -> c -> R -> blank."""
    with db.connect() as conn:
        tx = conn.execute("SELECT cleared FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if tx:
            nxt = {"": "c", "c": "R", "R": ""}.get(tx["cleared"], "")
            conn.execute("UPDATE transactions SET cleared = ? WHERE id = ?", (nxt, tx_id))
    return back("register")


# ---------------------------------------------------------------- budget (Budget sheet)

@app.route("/budget", methods=["GET", "POST"])
def budget():
    year = int(request.values.get("year") or date.today().year)
    with db.connect() as conn:
        if request.method == "POST":
            action = request.form.get("action", "save")
            months = [f"{year:04d}-{m:02d}" for m in range(1, 13)]
            if action == "save":
                for c in conn.execute("SELECT id FROM categories WHERE kind != 'transfer'").fetchall():
                    for i, month in enumerate(months):
                        amt = parse_money(request.form.get(f"b_{c['id']}_{i}"))
                        if amt:
                            conn.execute(
                                "INSERT INTO budgets (category_id, month, amount) VALUES (?, ?, ?) "
                                "ON CONFLICT(category_id, month) DO UPDATE SET amount = excluded.amount",
                                (c["id"], month, amt))
                        else:
                            conn.execute("DELETE FROM budgets WHERE category_id = ? AND month = ?", (c["id"], month))
                db.set_setting(conn, f"start_balance_{year}", str(parse_money(request.form.get("start_balance")) or 0))
                flash(f"{year} budget saved.", "ok")
            elif action == "copy_last_year":
                n = conn.execute(
                    """INSERT OR REPLACE INTO budgets (category_id, month, amount)
                       SELECT category_id, ? || substr(month, 5), amount FROM budgets WHERE month LIKE ?""",
                    (str(year), f"{year - 1:04d}-%")).rowcount
                flash(f"Copied {n} amounts from {year - 1}." if n else f"There's no {year - 1} budget to copy.",
                      "ok" if n else "error")
            return redirect(url_for("budget", year=year))

        months, grid = reports.budget_grid(conn, year)
        cats = [c for c in all_categories(conn) if c["kind"] != "transfer"]
        start_balance = float(db.get_setting(conn, f"start_balance_{year}", "0") or 0)
    inc = [sum(grid.get(c["id"], [0] * 12)[i] for c in cats if c["kind"] == "income") for i in range(12)]
    exp = [sum(grid.get(c["id"], [0] * 12)[i] for c in cats if c["kind"] == "expense") for i in range(12)]
    proj, bal = [], start_balance
    for i in range(12):
        bal += inc[i] - exp[i]
        proj.append(bal)
    return render_template("budget.html", year=year, months=months, grid=grid, cats=cats,
                           inc=inc, exp=exp, proj=proj, start_balance=start_balance)


# ---------------------------------------------------------------- reports

@app.route("/reports")
def reports_page():
    view = request.args.get("view", "monthly")
    with db.connect() as conn:
        if view == "yearly":
            year = int(request.args.get("year") or date.today().year)
            start_balance = float(db.get_setting(conn, f"start_balance_{year}", "0") or 0)
            data = reports.yearly(conn, year, start_balance)
            return render_template("reports.html", view=view, year=year, y=data, start_balance=start_balance)
        if view == "period":
            period = "weekly" if request.args.get("period") == "weekly" else "biweekly"
            days = 14 if period == "biweekly" else 7
            try:
                start = date.fromisoformat(request.args.get("start", ""))
                db.set_setting(conn, "period_anchor", start.isoformat())
            except ValueError:
                saved = db.get_setting(conn, "period_anchor")
                start = date.fromisoformat(saved) if saved else date.today() - timedelta(days=(date.today().weekday() + 1) % 7)
                while start + timedelta(days=days) <= date.today():  # jump to the period containing today
                    start += timedelta(days=days)
            end = start + timedelta(days=days - 1)
            scale = 12 / (26 if period == "biweekly" else 52)
            rep = reports.budget_vs_actual(conn, start, end, budget_scale=scale, months=[reports.month_key(start)])
            return render_template("reports.html", view=view, rep=rep, period=period, start=start, end=end,
                                   prev=start - timedelta(days=days), nxt=start + timedelta(days=days))
        # monthly / year-to-date (Report sheet)
        first = parse_month(request.args.get("month"))
        ytd = request.args.get("ytd") == "1"
        start = date(first.year, 1, 1) if ytd else first
        end = reports.add_months(first, 1) - timedelta(days=1)
        rep = reports.budget_vs_actual(conn, start, end)
    return render_template("reports.html", view="monthly", rep=rep, ytd=ytd, month=reports.month_key(first),
                           label=(f"January - {first.strftime('%B %Y')}" if ytd else first.strftime("%B %Y")),
                           prev=reports.month_key(reports.add_months(first, -1)),
                           nxt=reports.month_key(reports.add_months(first, 1)), start=start, end=end)


# ---------------------------------------------------------------- accounts (Accounts sheet)

@app.route("/accounts", methods=["GET", "POST"])
def accounts():
    with db.connect() as conn:
        if request.method == "POST":
            action = request.form.get("action")
            if action == "add":
                name = request.form.get("name", "").strip()
                if not name:
                    flash("Give the account a name.", "error")
                elif conn.execute("SELECT 1 FROM accounts WHERE lower(name) = lower(?)", (name,)).fetchone():
                    flash(f"There's already an account called \"{name}\".", "error")
                else:
                    kind = request.form.get("kind", "checking")
                    acc_id = conn.execute(
                        "INSERT INTO accounts (name, kind, goal, auto_opening) VALUES (?, ?, ?, 0)",
                        (name, kind if kind in ACCOUNT_KINDS else "other", parse_money(request.form.get("goal")))).lastrowid
                    opening = parse_money(request.form.get("opening"))
                    if opening:
                        conn.execute(
                            """INSERT INTO transactions (account_id, date, payee, amount, cleared, category_id,
                                                         category_locked, payee_locked) VALUES (?, ?, ?, ?, 'R', ?, 1, 1)""",
                            (acc_id, date.today().isoformat(), db.BEGINNING, opening,
                             db.ensure_category(conn, db.BEGINNING, "transfer")))
                    flash(f"Added \"{name}\".", "ok")
            elif action == "save":
                for a in conn.execute("SELECT id FROM accounts").fetchall():
                    i = a["id"]
                    name = request.form.get(f"name_{i}", "").strip()
                    kind = request.form.get(f"kind_{i}", "checking")
                    if not name:
                        continue
                    try:
                        conn.execute("UPDATE accounts SET name=?, kind=?, goal=?, sort=?, closed=? WHERE id=?",
                                     (name, kind if kind in ACCOUNT_KINDS else "other",
                                      parse_money(request.form.get(f"goal_{i}")),
                                      int(parse_money(request.form.get(f"sort_{i}")) or 0),
                                      1 if request.form.get(f"closed_{i}") else 0, i))
                    except Exception:
                        flash(f"There's already an account called \"{name}\".", "error")
                    plaid.refresh_opening_balance(conn, i)
                flash("Accounts saved.", "ok")
            elif action == "delete":
                acc = conn.execute("SELECT * FROM accounts WHERE id = ?", (int(request.form.get("id", 0)),)).fetchone()
                if acc and acc["plaid_account_id"]:
                    flash("That account comes from your bank. Disconnect the bank below instead.", "error")
                elif acc:
                    conn.execute("DELETE FROM accounts WHERE id = ?", (acc["id"],))
                    flash(f"Deleted \"{acc['name']}\" and its transactions.", "ok")
            elif action == "link":
                try:
                    sync_from = plaid.link_to_existing(int(request.form.get("bank_account", 0)),
                                                       int(request.form.get("target", 0)))
                    flash("Linked. Bank transactions now go into that account"
                          + (f" from {sync_from} on." if sync_from else "."), "ok")
                except ValueError as e:
                    flash(str(e), "error")
            elif action == "match_bank":
                acc = conn.execute("SELECT * FROM accounts WHERE id = ?", (int(request.form.get("id", 0)),)).fetchone()
                target = plaid.bank_balance_in_register_sign(acc) if acc else None
                if target is not None:
                    clr = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE account_id = ? "
                                       "AND cleared != ''", (acc["id"],)).fetchone()[0]
                    diff = round(target - clr, 2)
                    if diff:
                        conn.execute(
                            """INSERT INTO transactions (account_id, date, payee, amount, cleared, category_id,
                                                         category_locked, payee_locked) VALUES (?, ?, ?, ?, 'R', ?, 1, 1)""",
                            (acc["id"], date.today().isoformat(), "Balance adjustment", diff,
                             db.ensure_category(conn, db.BEGINNING, "transfer")))
                        flash(f"Added a {money(diff)} adjustment so the cleared balance matches the bank.", "ok")
            elif action == "remove_bank":
                plaid.remove_item(request.form.get("item_id", ""), request.form.get("delete_data") == "1")
                flash("Bank disconnected.", "ok")
            return redirect(url_for("accounts"))

        accts = all_accounts(conn)
        balances = account_balances(conn)
        items = conn.execute("SELECT * FROM items ORDER BY institution_name").fetchall()
    return render_template("accounts.html", accounts=accts, balances=balances, items=items, kinds=ACCOUNT_KINDS,
                           bank_sign=plaid.bank_balance_in_register_sign)


@app.post("/sync")
def sync():
    run_sync_and_flash()
    return back()


# ---------------------------------------------------------------- categories & rules

@app.route("/categories", methods=["GET", "POST"])
def categories_page():
    with db.connect() as conn:
        if request.method == "POST":
            action = request.form.get("action")
            if action == "save":
                for c in conn.execute("SELECT id FROM categories").fetchall():
                    i = c["id"]
                    name = request.form.get(f"name_{i}", "").strip()
                    kind = request.form.get(f"kind_{i}", "expense")
                    if name:
                        try:
                            conn.execute("UPDATE categories SET name=?, kind=?, sort=? WHERE id=?",
                                         (name, kind if kind in ("income", "expense", "transfer") else "expense",
                                          int(parse_money(request.form.get(f"sort_{i}")) or 0), i))
                        except Exception:
                            flash(f"There's already a category called \"{name}\".", "error")
                flash("Categories saved.", "ok")
            elif action == "add":
                name = request.form.get("name", "").strip()
                kind = request.form.get("kind", "expense")
                if name:
                    try:
                        conn.execute("INSERT INTO categories (name, kind, sort) VALUES (?, ?, 500)", (name, kind))
                        flash(f"Added \"{name}\".", "ok")
                    except Exception:
                        flash(f"There's already a category called \"{name}\".", "error")
            elif action == "delete":
                conn.execute("DELETE FROM categories WHERE id = ?", (int(request.form.get("id", 0)),))
                flash("Category deleted. Its transactions are now uncategorized.", "ok")
            elif action == "add_rule":
                match = request.form.get("match", "").strip().lower()
                cat = request.form.get("category_id", "")
                if match:
                    conn.execute(
                        "INSERT INTO rules (match, payee, category_id) VALUES (?, ?, ?) "
                        "ON CONFLICT(match) DO UPDATE SET payee = excluded.payee, category_id = excluded.category_id",
                        (match, request.form.get("payee", "").strip() or None, int(cat) if cat.isdigit() else None))
                    flash(f"Rule saved ({db.reapply_rules(conn)} bank transactions updated).", "ok")
            elif action == "delete_rule":
                conn.execute("DELETE FROM rules WHERE id = ?", (int(request.form.get("id", 0)),))
                flash("Rule removed.", "ok")
            elif action == "reapply":
                flash(f"{db.reapply_rules(conn)} bank transactions re-sorted.", "ok")
            return redirect(url_for("categories_page"))
        cats = all_categories(conn)
        counts = {r["category_id"]: r["n"] for r in conn.execute(
            "SELECT category_id, COUNT(*) AS n FROM transactions GROUP BY category_id")}
        rules = conn.execute(
            "SELECT r.*, c.name AS category FROM rules r LEFT JOIN categories c ON c.id = r.category_id ORDER BY r.match"
        ).fetchall()
    return render_template("categories.html", cats=cats, counts=counts, rules=rules)


# ---------------------------------------------------------------- settings

# ---------------------------------------------------------------- export (Excel workbook)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def export_choices(src, accounts):
    """Dates, accounts and sheets from the form (or the address after saving); defaults: this year, everything."""
    today = date.today()
    try:
        start = date.fromisoformat(src.get("from", ""))
    except ValueError:
        start = date(today.year, 1, 1)
    try:
        end = date.fromisoformat(src.get("to", ""))
    except ValueError:
        end = today
    if end < start:
        start, end = end, start
    known = [a["id"] for a in accounts]
    picked = src.getlist("account") if "account" in src or "sheet" in src else [str(i) for i in known]
    ids = [i for i in known if str(i) in picked]
    sheets = [k for k in export.SHEET_KEYS if k in src.getlist("sheet")] if "sheet" in src or "account" in src \
        else list(export.SHEET_KEYS)
    return start, end, ids, sheets


def export_presets(earliest):
    today = date.today()
    this_month = today.replace(day=1)
    last_month = reports.add_months(this_month, -1)
    return [
        ("This month", this_month, today),
        ("Last month", last_month, this_month - timedelta(days=1)),
        ("This year", date(today.year, 1, 1), today),
        ("Last year", date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)),
        ("Last 12 months", reports.add_months(this_month, -11), today),
        ("All time", min(earliest or today, today), today),
    ]


@app.route("/export", methods=["GET", "POST"])
def export_page():
    with db.connect() as conn:
        accounts = all_accounts(conn)
        start, end, ids, sheets = export_choices(request.form if request.method == "POST" else request.args, accounts)
        keep = {"from": start.isoformat(), "to": end.isoformat(), "account": ids, "sheet": sheets}
        if request.method == "POST":
            if not ids or not sheets:
                flash("Pick at least one account and one sheet.", "error")
                return redirect(url_for("export_page", **keep))
            data = export.build(conn, start, end, ids, sheets)
            filename = f"Budget {start.isoformat()} to {end.isoformat()}.xlsx"
            window = webview.windows[0] if webview.windows else None
            if window is None:  # running in a normal browser (developers): just download it
                return send_file(io.BytesIO(data), mimetype=XLSX, as_attachment=True, download_name=filename)
            folder = db.get_setting(conn, "export_dir") or os.path.join(os.path.expanduser("~"), "Downloads")
            if not os.path.isdir(folder):
                folder = os.path.expanduser("~")
            kind = webview.FileDialog.SAVE if hasattr(webview, "FileDialog") else webview.SAVE_DIALOG
            chosen = window.create_file_dialog(kind, directory=folder, save_filename=filename,
                                               file_types=("Excel workbook (*.xlsx)",))
            if not chosen:  # cancelled
                return redirect(url_for("export_page", **keep))
            path = chosen if isinstance(chosen, str) else chosen[0]
            if not path.lower().endswith(".xlsx"):
                path += ".xlsx"
            try:
                with open(path, "wb") as f:
                    f.write(data)
            except OSError as e:
                flash(f"Couldn't save the spreadsheet ({e.strerror or e}). If that file is open in Excel, "
                      "close it and try again, or pick another name.", "error")
                return redirect(url_for("export_page", **keep))
            db.set_setting(conn, "export_dir", os.path.dirname(path))
            db.set_setting(conn, "last_export", path)
            return redirect(url_for("export_page", saved=1, **keep))
        earliest = conn.execute("SELECT MIN(date) FROM transactions").fetchone()[0]
        presets = export_presets(date.fromisoformat(earliest[:10]) if earliest else None)
        info = export.preview(conn, start, end, ids)
        last = db.get_setting(conn, "last_export") if request.args.get("saved") else None
    return render_template("export.html", accounts=accounts, start=start, end=end, ids=ids, sheets=sheets,
                           sheet_list=export.SHEETS, presets=presets, info=info,
                           saved=last if last and os.path.isfile(last) else None)


@app.route("/api/export-preview")
def api_export_preview():
    with db.connect() as conn:
        start, end, ids, _ = export_choices(request.args, all_accounts(conn))
        return jsonify(export.preview(conn, start, end, ids))


@app.route("/export/open", methods=["POST"])
def export_open():
    """Open the spreadsheet you just saved, or show it in File Explorer. Only ever the last saved file."""
    with db.connect() as conn:
        path = db.get_setting(conn, "last_export")
    if not path or not os.path.isfile(path):
        flash("That spreadsheet isn't there anymore.", "error")
    elif request.form.get("action") == "folder":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        try:
            os.startfile(path)
        except OSError:
            flash("Couldn't open it. Is Excel (or another spreadsheet app) installed?", "error")
    return back("export_page")


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        with db.connect() as conn:
            for key in ("plaid_client_id", "plaid_env", "plaid_country_codes", "sync_interval_hours"):
                db.set_setting(conn, key, request.form.get(key, "").strip())
            secret = request.form.get("plaid_secret", "").strip()
            if secret:  # blank = keep the saved secret
                db.set_setting(conn, "plaid_secret", secret)
        flash("Settings saved.", "ok")
        return redirect(url_for("settings"))
    return render_template(
        "settings.html",
        client_id=plaid.cfg("plaid_client_id"), has_secret=bool(plaid.cfg("plaid_secret")),
        env=plaid.env(), countries=plaid.cfg("plaid_country_codes"),
        interval=plaid.cfg("sync_interval_hours"), data_dir=DATA_DIR,
    )


# ---------------------------------------------------------------- updates

@app.post("/update/check")
def update_check():
    st = updater.check(force=True)
    if st["error"]:
        flash(st["error"], "error")
    elif st["available"]:
        flash(f"Version {st['latest']} is available.", "ok")
    elif st["latest"]:
        flash(f"You're up to date (version {st['current']}).", "ok")
    else:
        flash("No releases have been published yet.", "ok")
    return back("settings")


@app.post("/update/install")
def update_install():
    try:
        new_dir, work = updater.prepare()
        updater.launch_swap(new_dir, work)
    except Exception as e:
        flash(f"Update failed: {e}", "error")
        return back("settings")
    # Quit so the helper can replace the program files; it reopens the app when it's done.
    threading.Timer(1.5, lambda: os._exit(0)).start()
    return render_template("updating.html", version=updater.status()["latest"])


# ---------------------------------------------------------------- Plaid Link
# Plaid Link (the bank sign-in screen) runs in your normal web browser, where bank security
# redirects work reliably. The app window opens it and waits for DATA_VERSION to change.

def bump_data_version():
    DATA_VERSION[0] += 1


@app.post("/api/open-link")
def api_open_link():
    if not plaid.configured():
        return jsonify(error="Add your Plaid keys on the Settings screen first."), 400
    item_id = (request.get_json(silent=True) or {}).get("item_id") or ""
    link_url = f"{BASE_URL}/link?key={LAUNCH_KEY}&browser=1" + (f"&item={item_id}" if item_id else "")
    webbrowser.open(link_url)
    return jsonify(ok=True, version=DATA_VERSION[0])


@app.get("/api/data-version")
def api_data_version():
    return jsonify(version=DATA_VERSION[0])


@app.get("/link")
def link_page():
    return render_template("link.html", item_id=request.args.get("item", ""))


@app.post("/api/link-token")
def api_link_token():
    if not plaid.configured():
        return jsonify(error="Add your Plaid keys on the Settings screen first."), 400
    item_id = (request.get_json(silent=True) or {}).get("item_id")
    access_token = None
    if item_id:
        with db.connect() as conn:
            row = conn.execute("SELECT access_token FROM items WHERE item_id = ?", (item_id,)).fetchone()
        if not row:
            return jsonify(error="Unknown connection."), 404
        access_token = row["access_token"]
    try:
        return jsonify(link_token=plaid.create_link_token(access_token))
    except Exception as e:
        return jsonify(error=str(e)), 502


@app.post("/api/exchange")
def api_exchange():
    body = request.get_json(silent=True) or {}
    try:
        item_id = plaid.exchange_public_token(body.get("public_token", ""), body.get("institution") or "Bank")
        result = plaid.sync_item(item_id)
    except Exception as e:
        return jsonify(error=str(e)), 502
    msg = (f"Bank connected. Pulled {result['added']} transactions"
           + (" (more may appear in a few minutes - click Sync now)." if result["added"] == 0 else ".")
           + " If an account is one you already track, link it on the Accounts page.")
    WINDOW_MESSAGES.append((msg, "ok"))
    bump_data_version()
    return jsonify(ok=True, message=msg)


@app.post("/api/reconnected")
def api_reconnected():
    item_id = (request.get_json(silent=True) or {}).get("item_id", "")
    try:
        plaid.sync_item(item_id)
    except Exception as e:
        return jsonify(error=str(e)), 502
    WINDOW_MESSAGES.append(("Reconnected and synced.", "ok"))
    bump_data_version()
    return jsonify(ok=True, message="Reconnected and synced.")


# ---------------------------------------------------------------- background server + auto-sync

def auto_sync_loop():
    while True:
        try:
            updater.check()  # at most every 6 hours; shows a banner when there's a new version
        except Exception as e:
            print(f"[update-check] failed: {e}")
        if plaid.configured():
            try:
                totals, errors = plaid.sync_all()
                print(f"[auto-sync] {totals}" + (f" errors: {errors}" if errors else ""))
            except Exception as e:
                print(f"[auto-sync] failed: {e}")
        try:
            hours = float(plaid.cfg("sync_interval_hours") or 6)
        except ValueError:
            hours = 6
        time.sleep(max(hours, 0.25) * 3600)


def start_server():
    """Start the local server on a free port in a background thread. Returns the window's start URL."""
    global BASE_URL
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", 0, app, threaded=True)
    BASE_URL = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=auto_sync_loop, daemon=True).start()
    return f"{BASE_URL}/?key={LAUNCH_KEY}"


if __name__ == "__main__":
    # Developer mode: open in your browser instead of the app window.
    start_url = start_server()
    print("Budget Tool (developer mode):", start_url)
    webbrowser.open(start_url)
    threading.Event().wait()
