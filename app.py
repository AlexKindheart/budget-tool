"""Budget Tool screens and actions (Flask). Shown inside the desktop window by budget_tool.py.

The server only listens on 127.0.0.1 and every request needs the per-launch key
(LAUNCH_KEY), so other programs and websites can't read your data. On top of that the budget is encrypted and
Budget Tool starts locked: nothing is read until you enter your password (security.py).
"""
import io
import json
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

import bills
import db
import duplicates
import export
import paths
import plaid_client as plaid
import reports
import security
import updater
from paths import DATA_DIR, RESOURCE_DIR

app = Flask(__name__,
            template_folder=os.path.join(RESOURCE_DIR, "templates"),
            static_folder=os.path.join(RESOURCE_DIR, "static"))
app.secret_key = secrets.token_hex(32)  # new each launch; sessions only live while the app is open
app.config["SESSION_COOKIE_SAMESITE"] = "Strict"

LAUNCH_KEY = secrets.token_urlsafe(24)
BASE_URL = ""          # set by start_server()
DATA_VERSION = [0]     # bumped when a bank is (re)connected, so the window knows to refresh
STARTUP_SYNC = {"running": False, "changed": 0}  # the one bank sync each time Budget Tool opens
WINDOW_MESSAGES = []   # messages from the browser sign-in, shown in the app window next time it loads
LOCK = {"minutes": 15, "last_active": time.time(), "failures": 0, "started": False, "protected": True}
NO_PASSWORD_WARNING = ("Without a password, anyone who uses this computer, or gets a copy of the Budget Tool folder or "
                       "the USB drive it's on, can open your budget: every transaction, your account balances, and the "
                       "connections to your bank. Banking information in the wrong hands is a bad idea.")
LOCK_CHOICES = [5, 15, 30, 60, 0]  # minutes without use before Budget Tool locks itself (0 = only when closed)
PASSWORD_GATE = threading.Lock()   # one password attempt at a time
OPEN_WHILE_LOCKED = {"static", "unlock", "setup_password", "recover", "lock"}
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
def require_unlock():
    """Locked: only the password screens. Unlocked: lock again after a while without use."""
    ep = request.endpoint
    if not db.unlocked() and ep != "static" and security.is_set_up() and not security.has_password():
        finish_unlock(security.open_key())  # you chose not to use a password: open straight away
    if db.unlocked():
        idle = LOCK["minutes"] * 60 if LOCK["protected"] else 0
        if idle and time.time() - LOCK["last_active"] > idle + 30 and ep not in OPEN_WHILE_LOCKED:
            lock_now()
            flash(f"Budget Tool locked itself after {LOCK['minutes']} minutes without use.", "ok")
        elif ep != "static" and (not request.path.startswith("/api/") or ep == "api_still_here"):
            LOCK["last_active"] = time.time()
    if db.unlocked() or ep in OPEN_WHILE_LOCKED:
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="Budget Tool is locked. Enter your password in the Budget Tool window.", locked=True), 401
    nxt = request.full_path if request.method == "GET" else None
    return redirect(url_for("unlock" if security.is_set_up() else "setup_password", next=nxt))


@app.after_request
def no_caching(resp):
    resp.headers["Cache-Control"] = "no-store"  # nothing from your budget is kept in a browser cache
    return resp


@app.before_request
def deliver_window_messages():
    if request.method == "GET" and request.args.get("browser") != "1" and not request.path.startswith(("/api/", "/static/")):
        while WINDOW_MESSAGES:
            flash(*WINDOW_MESSAGES.pop(0))


@app.context_processor
def inject_globals():
    if not db.unlocked():
        return {"csrf_token": csrf_token, "locked": True, "plaid_env": None, "plaid_ready": False, "syncing": False,
                "today": date.today(), "update": {}, "lock_minutes": 0}
    return {"csrf_token": csrf_token, "locked": False, "plaid_env": plaid.env(), "plaid_ready": plaid.configured(),
            "syncing": STARTUP_SYNC["running"], "protected": LOCK["protected"],
            "lock_minutes": LOCK["minutes"] if LOCK["protected"] else 0,
            "today": date.today(), "update": updater.status()}


@app.template_filter("money")
def money(value):
    value = value or 0
    sign = "-" if value < -0.004 else ""
    return f"{sign}${abs(value):,.2f}"


@app.template_filter("money_short")
def money_short(value):
    """Like money, but without ".00" on whole-dollar amounts (keeps the Budget grid narrow)."""
    value = round(value or 0, 2)
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}" if value == int(value) else f"{sign}${abs(value):,.2f}"


@app.template_filter("amt")
def amt(value):
    """An amount for an input box: blank for zero, "1500" for whole dollars, "12.50" otherwise."""
    if not value:
        return ""
    value = round(value, 2)
    return str(int(value)) if value == int(value) else f"{value:.2f}"


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


def check_category(conn, category_id, amount, current=None, when=None):
    """Deposits take income categories, payments take expense categories; non-budget categories
    ([Transfer], [Beginning Balance]...) and Uncategorized fit either. The category must be one that's used in
    the transaction's year (`when`, a 'YYYY-MM-DD' date). A category the transaction already has is left alone,
    so editing the memo of an older transaction still works."""
    if category_id is None or category_id == current:
        return
    row = conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
    if not row:
        raise ValueError("That category doesn't exist anymore.")
    if when and not db.active_in(row, int(str(when)[:4])):
        raise ValueError(f"\"{row['name']}\" isn't used in {str(when)[:4]}. Pick one of that year's categories.")
    if not amount:
        return
    want = "income" if amount > 0 else "expense"
    if row["kind"] not in (want, "transfer"):
        raise ValueError(f"\"{row['name']}\" is an {row['kind']} category, but this is a "
                         f"{'deposit' if amount > 0 else 'payment'}. Pick an {want} category "
                         "or a non-budget one like [Transfer].")


def category_suggestions(conn, txs):
    """{transaction id: (category id, name, why)}: the category you picked most recently for the same payee, if it
    still fits (income vs expense, and used in that transaction's year). Otherwise, when your bank marks it as a
    credit card payment going out, Debt; as a transfer, [Transfer]. Shown as a one-click button; nothing is
    categorized automatically."""
    if not txs:
        return {}
    cats = {c["id"]: c for c in conn.execute("SELECT * FROM categories")}
    last = {}
    for r in conn.execute("""SELECT lower(trim(payee)) AS p, amount > 0 AS dep, category_id FROM transactions
                             WHERE category_id IS NOT NULL AND is_opening = 0 AND payee IS NOT NULL AND payee != ''
                             ORDER BY date DESC, id DESC"""):
        last.setdefault((r["p"], r["dep"]), []).append(r["category_id"])
    out = {}
    for t in txs:
        year, want = int(t["date"][:4]), ("income" if t["amount"] > 0 else "expense")
        for cid in last.get(((t["payee"] or "").strip().lower(), 1 if t["amount"] > 0 else 0), []):
            c = cats.get(cid)
            if c and db.active_in(c, year) and c["kind"] in (want, "transfer"):
                out[t["id"]] = (cid, c["name"], f"You used this for {t['payee']} before. Click to use it again.")
                break
        if t["id"] in out:
            continue
        card_payment = t["pfc_detailed"] == "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"
        if card_payment and t["amount"] < 0:
            debt = next((c for c in cats.values() if c["name"].lower() == "debt" and c["kind"] == "expense"
                         and db.active_in(c, year)), None)
            if debt:
                out[t["id"]] = (debt["id"], debt["name"], "Your bank says this is a credit card payment. "
                                                          "Click to count it as debt paid.")
        elif card_payment or t["pfc_primary"] in ("TRANSFER_IN", "TRANSFER_OUT"):
            tr = next((c for c in cats.values() if c["name"] == db.TRANSFER and db.active_in(c, year)), None)
            if tr:
                out[t["id"]] = (tr["id"], tr["name"], "Your bank says this is a transfer. Transfers aren't income "
                                                      "or spending. Click to use [Transfer].")
    return out


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
    p = {"period": kind, "prev": None, "next": None, "choices": [], "selected": ""}
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


def setup_steps(conn):
    """First-run checklist on the Overview (hidden once all three are done)."""
    steps = [
        ("Add your Plaid keys, so Budget Tool can read your bank", plaid.configured(), "Settings", url_for("settings")),
        ("Connect your bank (or add an account by hand)",
         conn.execute("SELECT 1 FROM accounts").fetchone() is not None, "Accounts", url_for("accounts")),
        ("Set your monthly budget (one click fills it from your spending)",
         conn.execute("SELECT 1 FROM budgets").fetchone() is not None, "Budget", url_for("budget")),
    ]
    if all(done for _, done, _, _ in steps):
        return None
    first = next(i for i, st in enumerate(steps) if not st[1])
    return [(text, done, button, link, i == first) for i, (text, done, button, link) in enumerate(steps)]


def todo_items(conn):
    """What needs you right now, most important first: [(message, button, link, style)]. Empty = nothing to do."""
    items = []
    for it in conn.execute("SELECT * FROM items WHERE error IS NOT NULL AND error != ''"):
        items.append((f"{it['institution_name'] or 'Your bank'} needs you to sign in again before it can sync.",
                      "Reconnect", url_for("accounts"), "warn"))
    n = conn.execute("SELECT COUNT(*) FROM transactions t WHERE t.category_id IS NULL AND t.is_opening = 0 "
                     "AND NOT EXISTS (SELECT 1 FROM splits s WHERE s.tx_id = t.id)").fetchone()[0]
    if n:
        items.append((f"{n} transaction{'' if n == 1 else 's'} need{'s' if n == 1 else ''} a category.", "Sort them",
                      url_for("register", month="all", category="none"), "todo"))
    n = int(db.get_setting(conn, "combined_unseen", "0") or 0)
    if n:
        items.append((f"{n} bank transaction{' was' if n == 1 else 's were'} combined with one{'' if n == 1 else 's'} "
                      "you typed in yourself, so they only count once.", "Check them",
                      url_for("register", month="all", combined="1"), "todo"))
    balances = account_balances(conn)
    for a in conn.execute("SELECT * FROM accounts WHERE closed = 0 AND plaid_account_id IS NOT NULL"):
        bank = plaid.bank_balance_in_register_sign(a)
        posted = balances.get(a["id"], (0, 0))[1]
        if bank is not None and abs(bank - posted) > 0.005:
            items.append((f"{a['name']} doesn't match your bank (off by {money(bank - posted)}).", "Fix it",
                          url_for("accounts"), "warn"))
    alerts = reports.budget_alerts(conn)
    for a in alerts[:3]:
        if a["over"]:
            text = f"{a['name']} is over budget: {money(a['actual'])} spent of {money(a['budget'])}."
        else:
            text = (f"{a['name']}: {money(a['left'])} left with {a['days_left']} day{'' if a['days_left'] == 1 else 's'} "
                    f"to go. At this pace you'll spend about {money(a['pace'])} of {money(a['budget'])}.")
        items.append((text, "See spending", url_for("register", month=reports.month_key(date.today()), category=a["id"]),
                      "warn" if a["over"] else "todo"))
    if len(alerts) > 3:
        items.append((f"{len(alerts) - 3} more categor{'y is' if len(alerts) == 4 else 'ies are'} over or close to the budget.",
                      "Report", url_for("reports_page"), "todo"))
    for d in old_copies(conn):
        items.append((f"An old, unencrypted copy of your budget is still on this computer, in {d}.", "Review",
                      url_for("old_copy"), "warn"))
    month = reports.month_key(date.today())
    if (conn.execute("SELECT 1 FROM budgets").fetchone()
            and not conn.execute("SELECT 1 FROM budgets WHERE month = ?", (month,)).fetchone()):
        items.append((f"There's no budget for {date.today():%B} yet.", "Set it", url_for("budget", year=date.today().year), "todo"))
    return items


@app.route("/")
def overview():
    with db.connect() as conn:
        earliest = conn.execute("SELECT MIN(date) FROM transactions").fetchone()[0]
        p = overview_period(request.args, date.fromisoformat(earliest[:10]) if earliest else None)
        rep = reports.budget_vs_actual(conn, p["start"], p["end"])
        trend = reports.daily_balances(conn, start=p["start"], end=p["end"])  # the chart follows the picker
        trend["caption"] = {"all": "all time", "year": f"in {p['label']}", "month": f"in {p['label']}"}.get(p["period"], p["label"])
        accounts = all_accounts(conn, include_closed=False)
        balances = account_balances(conn)
        recent = conn.execute(
            """SELECT t.*, c.name AS category, a.name AS account FROM transactions t
               LEFT JOIN categories c ON c.id = t.category_id JOIN accounts a ON a.id = t.account_id
               WHERE t.is_opening = 0 ORDER BY t.date DESC, t.id DESC LIMIT 10""").fetchall()
        synced = last_synced(conn)
        has_items = conn.execute("SELECT 1 FROM items").fetchone() is not None
        plan_year = year_plan_prompt(conn)
        steps, todo = setup_steps(conn), todo_items(conn)
        bills.refresh(conn)
        coming = bills.upcoming(conn, 30)
        safe = bills.safe_to_spend(conn, balances) if conn.execute("SELECT 1 FROM bills").fetchone() else None
    return render_template(
        "overview.html", p=p, month=reports.month_key(p["start"] if p["period"] == "month" else date.today()),
        rep=rep, trend=trend, accounts=accounts, balances=balances, recent=recent, last_synced=synced,
        has_items=has_items, plan_year=plan_year, steps=steps, todo=todo, coming=coming[:8], safe=safe)


# ---------------------------------------------------------------- register (Transactions sheet)

@app.route("/register")
def register():
    month_raw = request.args.get("month", reports.month_key(date.today()))
    account = request.args.get("account", "")
    cat = request.args.get("category", "")
    q = request.args.get("q", "").strip()
    uncleared = request.args.get("uncleared") == "1"
    combined = request.args.get("combined") == "1"

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
        where.append("t.category_id IS NULL AND NOT EXISTS (SELECT 1 FROM splits s WHERE s.tx_id = t.id)")
    elif cat.isdigit():  # a split transaction shows up under each of its parts' categories
        where.append("(t.category_id = ? OR EXISTS (SELECT 1 FROM splits s WHERE s.tx_id = t.id AND s.category_id = ?))")
        params += [int(cat), int(cat)]
    if q:
        like = "%" + q.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        where.append("(t.payee LIKE ? ESCAPE '!' OR t.memo LIKE ? ESCAPE '!' OR t.bank_name LIKE ? ESCAPE '!')")
        params += [like, like, like]
    if uncleared:  # "Pending only": bank charges that haven't posted yet
        where.append("t.cleared = '' AND t.plaid_id IS NOT NULL")
    if combined:  # bank transactions combined with your own entries
        where.append("t.merged IS NOT NULL")

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
        parts = split_parts(conn, [t["id"] for t in txs])
        suggest = category_suggestions(conn, [t for t in txs if t["category_id"] is None and not t["is_opening"]
                                              and t["id"] not in parts])
        merged_notes = {t["id"]: duplicates.describe(t) for t in txs if t["merged"]}
        if combined:
            db.set_setting(conn, "combined_unseen", "0")  # you've seen them
    month_d = parse_month(month_raw) if month_raw not in ("all", "range") else date.today().replace(day=1)
    if span:
        label = f"{span[0]:%b} {span[0].day}, {span[0].year} – {span[1]:%b} {span[1].day}, {span[1].year}"
    else:
        label = "All dates" if month_raw == "all" else month_d.strftime("%B %Y")
    return render_template(
        "register.html", txs=txs, running=running, categories=categories, accounts=accounts,
        month=month_raw, label=label, span=span,
        prev=reports.month_key(reports.add_months(month_d, -1)), nxt=reports.month_key(reports.add_months(month_d, 1)),
        account=account, cat=cat, q=q, uncleared=uncleared, suggest=suggest, combined=combined,
        merged_notes=merged_notes, parts=parts,
        total_in=sum(t["amount"] for t in txs if t["amount"] > 0),
        total_out=-sum(t["amount"] for t in txs if t["amount"] < 0))


def split_parts(conn, tx_ids):
    """{transaction id: [{category, category_id, amount, memo}]} for the split ones among tx_ids."""
    out = {}
    ids = list(tx_ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in conn.execute(
                f"""SELECT s.*, c.name AS category FROM splits s LEFT JOIN categories c ON c.id = s.category_id
                    WHERE s.tx_id IN ({",".join("?" * len(chunk))}) ORDER BY s.id""", chunk):
            out.setdefault(r["tx_id"], []).append(
                {"category": r["category"] or "Uncategorized", "category_id": r["category_id"], "amount": r["amount"],
                 "memo": r["memo"] or ""})
    return out


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
    return {
        "account_id": int(acc), "date": d, "payee": request.form.get("payee", "").strip(),
        "memo": request.form.get("memo", "").strip(), "amount": round((dep or 0) - (pay or 0), 2),
        "category_id": int(cat) if cat.isdigit() else None,
        "cleared": "",  # pending/posted is tracked for bank transactions only; there's no Clr column anymore
    }


@app.post("/register/add")
def register_add():
    with db.connect() as conn:
        try:
            t = _tx_from_form(conn)
            check_category(conn, t["category_id"], t["amount"], when=t["date"])
        except ValueError as e:
            flash(str(e), "error")
            return back("register")
        conn.execute(
            """INSERT INTO transactions (account_id, date, payee, memo, amount, category_id, cleared,
                                         category_locked, payee_locked) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)""",
            (t["account_id"], t["date"], t["payee"], t["memo"], t["amount"], t["category_id"], t["cleared"]))
        combined = duplicates.match(conn, t["account_id"])
        plaid.refresh_opening_balance(conn, t["account_id"])
    if combined:
        flash("Added. Your bank had already sent this one, so the two were combined and it only counts once "
              "(look for the \"combined\" label; Not the same splits them).", "ok")
    else:
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
                t["cleared"] = tx["cleared"]  # keep pending / posted as it was
                if tx["plaid_id"]:  # the bank owns date/amount/account; you own payee/memo/category
                    t.update(account_id=tx["account_id"], date=tx["date"], amount=tx["amount"])
                same_side = (t["amount"] > 0) == (tx["amount"] > 0)
                is_split = bool(split_parts(conn, [tx_id]))
                if is_split:
                    t["category_id"] = None  # the parts have the categories
                else:
                    check_category(conn, t["category_id"], t["amount"], tx["category_id"] if same_side else None, t["date"])
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("register_edit", tx_id=tx_id, next=request.form.get("next")))
            conn.execute(
                """UPDATE transactions SET account_id=?, date=?, payee=?, memo=?, amount=?, category_id=?,
                   cleared=?, category_locked=1, payee_locked=1 WHERE id=?""",
                (t["account_id"], t["date"], t["payee"], t["memo"], t["amount"], t["category_id"], t["cleared"], tx_id))
            flash("Saved.", "ok")
            if is_split and abs(t["amount"] - tx["amount"]) > 0.005:
                conn.execute("DELETE FROM splits WHERE tx_id = ?", (tx_id,))
                flash("The amount changed, so the split was removed. Pick a category or split it again.", "error")
            if not tx["plaid_id"] and duplicates.match(conn, t["account_id"]):
                flash("Your bank had already sent this one, so the two were combined and it only counts once.", "ok")
            for acc_id in {tx["account_id"], t["account_id"]}:
                plaid.refresh_opening_balance(conn, acc_id)
            return redirect(request.form.get("next") or url_for("register"))
        categories = all_categories(conn)
        accounts = all_accounts(conn)
        parts = split_parts(conn, [tx_id]).get(tx_id, [])
    return render_template("tx_edit.html", tx=tx, categories=categories, accounts=accounts, parts=parts,
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


@app.post("/register/<int:tx_id>/split")
def register_split(tx_id):
    """"Not the same": a bank transaction that was combined with your own entry is split back into two."""
    with db.connect() as conn:
        tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if tx and duplicates.split(conn, tx_id):
            plaid.refresh_opening_balance(conn, tx["account_id"])
            flash("Split back into your entry and the bank's. They won't be combined again.", "ok")
    return redirect(request.form.get("next") or url_for("register"))


@app.post("/register/bulk-category")
def register_bulk_category():
    """Give several transactions the same category at once (the ticked rows on the Register)."""
    ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
    raw = request.form.get("category_id", "")
    category_id = int(raw) if raw.isdigit() else None
    done, skipped = 0, []
    with db.connect() as conn:
        for i in ids:
            tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (i,)).fetchone()
            if not tx or tx["is_opening"] or split_parts(conn, [i]):
                skipped.append(i)
                continue
            try:
                check_category(conn, category_id, tx["amount"], tx["category_id"], tx["date"])
            except ValueError:
                skipped.append(i)
                continue
            conn.execute("UPDATE transactions SET category_id = ?, category_locked = 1 WHERE id = ?", (category_id, i))
            done += 1
    if not ids:
        flash("Tick the transactions to change first.", "error")
    else:
        flash(f"Changed {done} transaction{'' if done == 1 else 's'}." + (
            f" {len(skipped)} didn't fit that category (a deposit needs an income category, a payment an expense one,"
            " split ones are changed on their own) and were left as they were." if skipped else ""),
            "ok" if done else "error")
    return back("register")


@app.post("/register/<int:tx_id>/splits")
def register_splits(tx_id):
    """Split one transaction across several categories, or stop splitting it."""
    edit = url_for("register_edit", tx_id=tx_id, next=request.form.get("next") or None)
    with db.connect() as conn:
        tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if not tx or tx["is_opening"]:
            abort(404)
        if request.form.get("action") == "remove":
            conn.execute("DELETE FROM splits WHERE tx_id = ?", (tx_id,))
            flash("No longer split. Pick a category for it.", "ok")
            return redirect(edit)
        parts = []
        try:
            for c, a, m in zip(request.form.getlist("split_category"), request.form.getlist("split_amount"),
                               request.form.getlist("split_memo")):
                amount = parse_money(a)
                if not c and not amount:
                    continue  # an empty row
                if not c.isdigit() or not amount:
                    raise ValueError("Each part needs a category and an amount.")
                if amount <= 0:
                    raise ValueError("Enter each part as a positive amount; it takes the transaction's side.")
                check_category(conn, int(c), tx["amount"], None, tx["date"])
                parts.append((int(c), round(amount, 2), m.strip()))
            if len(parts) < 2:
                raise ValueError("A split needs at least two parts.")
            total, whole = round(sum(a for _, a, _ in parts), 2), round(abs(tx["amount"]), 2)
            if abs(total - whole) > 0.005:
                raise ValueError(f"The parts add up to {money(total)}, but the transaction is {money(whole)} "
                                 f"({money(abs(whole - total))} {'left to split' if total < whole else 'too much'}).")
        except ValueError as e:
            flash(str(e), "error")
            return redirect(edit)
        sign = 1 if tx["amount"] > 0 else -1
        conn.execute("DELETE FROM splits WHERE tx_id = ?", (tx_id,))
        conn.executemany("INSERT INTO splits (tx_id, category_id, amount, memo) VALUES (?, ?, ?, ?)",
                         [(tx_id, c, sign * a, m) for c, a, m in parts])
        conn.execute("UPDATE transactions SET category_id = NULL, category_locked = 1 WHERE id = ?", (tx_id,))
    flash(f"Split into {len(parts)} parts.", "ok")
    return redirect(request.form.get("next") or url_for("register"))


@app.post("/register/<int:tx_id>/category")
def register_category(tx_id):
    cat_raw = request.form.get("category_id", "")
    category_id = int(cat_raw) if cat_raw.isdigit() else None
    with db.connect() as conn:
        tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if not tx:
            abort(404)
        quiet = request.headers.get("X-Requested-With") == "fetch"  # changed in place on the Register, no reload
        try:
            if split_parts(conn, [tx_id]):
                raise ValueError("This transaction is split. Open it (Edit) to change its parts.")
            check_category(conn, category_id, tx["amount"], tx["category_id"], tx["date"])
        except ValueError as e:
            if quiet:
                return jsonify(error=str(e)), 400
            flash(str(e), "error")
            return back("register")
        conn.execute("UPDATE transactions SET category_id = ?, category_locked = 1 WHERE id = ?", (category_id, tx_id))
        if quiet:
            return jsonify(ok=True)
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
                for c in [c for c in reports.categories_in_year(conn, year) if c["kind"] != "transfer"]:
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
            elif action == "from_spending":
                # Each of this year's categories: average per month over the last 12 full months (including the
                # category it replaced, if renamed), rounded up to $5. Only fills months that are still empty.
                first = reports.add_months(date.today().replace(day=1), -12)
                last = date.today().replace(day=1) - timedelta(days=1)
                act = reports.actuals(conn, first, last)
                filled = 0
                for c in reports.categories_in_year(conn, year):
                    if c["kind"] == "transfer":
                        continue
                    total = act.get(c["id"], 0) + (act.get(c["replaces"], 0) if c["replaces"] else 0)
                    avg = -(-max(total, 0) / 12 // 5) * 5  # round up to the next $5
                    if avg <= 0:
                        continue
                    n = 0
                    for m in months:
                        n += conn.execute("INSERT OR IGNORE INTO budgets (category_id, month, amount) VALUES (?, ?, ?)",
                                          (c["id"], m, avg)).rowcount
                    filled += 1 if n else 0
                flash(f"Filled {filled} categories with your average monthly amounts from the last 12 months. "
                      "Check them and change anything that looks off." if filled else
                      "Nothing to fill: either there's no spending in the last 12 months yet, or every month already has a budget.",
                      "ok" if filled else "error")
            elif action == "copy_last_year":
                # Each of this year's categories takes last year's amounts from itself, or from the category it
                # replaced (renamed / changed for this year). Categories retired before this year are skipped.
                n = 0
                for c in reports.categories_in_year(conn, year):
                    src = c["id"]
                    if c["active_from"] == year and c["replaces"]:
                        src = c["replaces"]
                    n += conn.execute(
                        """INSERT OR REPLACE INTO budgets (category_id, month, amount)
                           SELECT ?, ? || substr(month, 5), amount FROM budgets WHERE category_id = ? AND month LIKE ?""",
                        (c["id"], str(year), src, f"{year - 1:04d}-%")).rowcount
                flash(f"Copied {n} amounts from {year - 1}." if n else f"There's no {year - 1} budget to copy.",
                      "ok" if n else "error")
            return redirect(url_for("budget", year=year))

        months, grid = reports.budget_grid(conn, year)
        cats = [c for c in reports.categories_in_year(conn, year) if c["kind"] != "transfer"]
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
        if view == "payees":
            if request.args.get("month"):
                first = parse_month(request.args.get("month"))
                start, end, label = first, reports.add_months(first, 1) - timedelta(days=1), first.strftime("%B %Y")
                prev = url_for("reports_page", view="payees", month=reports.month_key(reports.add_months(first, -1)))
                nxt = url_for("reports_page", view="payees", month=reports.month_key(reports.add_months(first, 1)))
            else:
                year = int(request.args.get("year") or date.today().year)
                start, end, label = date(year, 1, 1), date(year, 12, 31), str(year)
                prev, nxt = url_for("reports_page", view="payees", year=year - 1), url_for("reports_page", view="payees", year=year + 1)
            rows = reports.payees(conn, start, end)
            return render_template("reports.html", view=view, payees=rows, label=label, prev=prev, nxt=nxt,
                                   start=start, end=end, by_month=bool(request.args.get("month")),
                                   total=sum(r["spent"] for r in rows))
        if view == "yearly":
            year = int(request.args.get("year") or date.today().year)
            data = reports.yearly(conn, year)  # balances come from your accounts, not the Budget screen's estimate
            return render_template("reports.html", view=view, year=year, y=data)
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
            rep = reports.budget_vs_actual(conn, start, end)
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
                        flash(f"Added a {money(diff)} adjustment so the posted balance matches the bank.", "ok")
            elif action == "remove_bank":
                plaid.remove_item(request.form.get("item_id", ""), request.form.get("delete_data") == "1")
                flash("Bank disconnected.", "ok")
            return redirect(url_for("accounts"))

        accts = all_accounts(conn)
        balances = account_balances(conn)
        items = conn.execute("SELECT * FROM items ORDER BY institution_name").fetchall()
    return render_template("accounts.html", accounts=accts, balances=balances, items=items, kinds=ACCOUNT_KINDS,
                           bank_sign=plaid.bank_balance_in_register_sign)


# ---------------------------------------------------------------- categories

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
                        me = conn.execute("SELECT * FROM categories WHERE id = ?", (i,)).fetchone()
                        if db.name_clash(conn, name, me["active_from"], me["active_until"], ignore_id=i):
                            flash(f"There's already a category called \"{name}\" in the same years.", "error")
                            continue
                        conn.execute("UPDATE categories SET name=?, kind=?, sort=?, rollover=? WHERE id=?",
                                     (name, kind if kind in ("income", "expense", "transfer") else "expense",
                                      int(parse_money(request.form.get(f"sort_{i}")) or 0),
                                      1 if request.form.get(f"rollover_{i}") == "1" else 0, i))
                flash("Categories saved.", "ok")
            elif action == "add":
                name = request.form.get("name", "").strip()
                kind = request.form.get("kind", "expense")
                if name and db.name_clash(conn, name):
                    flash(f"There's already a category called \"{name}\".", "error")
                elif name:
                    conn.execute("INSERT INTO categories (name, kind, sort) VALUES (?, ?, 500)", (name, kind))
                    flash(f"Added \"{name}\".", "ok")
            elif action == "delete":
                conn.execute("DELETE FROM categories WHERE id = ?", (int(request.form.get("id", 0)),))
                flash("Category deleted. Its transactions are now uncategorized.", "ok")
            return redirect(url_for("categories_page"))
        cats = all_categories(conn)
        counts = {r["category_id"]: r["n"] for r in conn.execute(
            "SELECT category_id, COUNT(*) AS n FROM lines GROUP BY category_id")}
    return render_template("categories.html", cats=cats, counts=counts, next_year=date.today().year + 1)


# ---------------------------------------------------------------- bills and paychecks that repeat

@app.route("/bills", methods=["GET", "POST"])
def bills_page():
    with db.connect() as conn:
        if request.method == "POST":
            action = request.form.get("action")
            if action == "delete":
                conn.execute("DELETE FROM bills WHERE id = ?", (int(request.form.get("id", 0) or 0),))
                flash("Removed.", "ok")
                return redirect(url_for("bills_page"))
            try:
                name = request.form.get("name", "").strip()
                amount = parse_money(request.form.get("amount"))
                every = request.form.get("every", "month")
                kind = "income" if request.form.get("kind") == "income" else "bill"
                acc, cat = request.form.get("account_id", ""), request.form.get("category_id", "")
                if not name:
                    raise ValueError("Give it a name.")
                if not amount or amount <= 0:
                    raise ValueError("Enter the amount (a positive number).")
                if every not in dict(bills.EVERY):
                    raise ValueError("Pick how often it repeats.")
                try:
                    nxt = date.fromisoformat(request.form.get("next_date", "")).isoformat()
                except ValueError:
                    raise ValueError("Enter the next date it's due.")
                values = (name, round(abs(amount), 2), kind, int(acc) if acc.isdigit() else None,
                          int(cat) if cat.isdigit() else None, every, nxt)
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("bills_page"))
            if action == "save" and request.form.get("id", "").isdigit():
                conn.execute("""UPDATE bills SET name=?, amount=?, kind=?, account_id=?, category_id=?, every=?,
                                next_date=? WHERE id=?""", values + (int(request.form["id"]),))
                flash("Saved.", "ok")
            else:
                conn.execute("""INSERT INTO bills (name, amount, kind, account_id, category_id, every, next_date)
                                VALUES (?, ?, ?, ?, ?, ?, ?)""", values)
                flash(f"Added {name}.", "ok")
            return redirect(url_for("bills_page"))
        bills.refresh(conn)
        rows = conn.execute("""SELECT b.*, a.name AS account, c.name AS category FROM bills b
                               LEFT JOIN accounts a ON a.id = b.account_id LEFT JOIN categories c ON c.id = b.category_id
                               ORDER BY b.kind DESC, b.next_date, lower(b.name)""").fetchall()
        prefill = None
        src = request.args.get("from_tx", "")
        if src.isdigit():  # "Make this a repeating bill" from a transaction
            t = conn.execute("SELECT * FROM transactions WHERE id = ?", (int(src),)).fetchone()
            if t:
                prefill = {"name": t["payee"] or "", "amount": abs(t["amount"]),
                           "kind": "income" if t["amount"] > 0 else "bill", "account_id": t["account_id"],
                           "category_id": t["category_id"], "every": "month",
                           "next_date": bills.step(date.fromisoformat(t["date"][:10]), "month")}
        coming = bills.upcoming(conn, 30)
        safe = bills.safe_to_spend(conn, account_balances(conn))
        categories = all_categories(conn)
        accounts = all_accounts(conn, include_closed=False)
    return render_template("bills.html", rows=rows, coming=coming, safe=safe, categories=categories,
                           accounts=accounts, every=bills.EVERY, prefill=prefill)


# ---------------------------------------------------------------- balancing a hand-kept account against a statement

@app.route("/accounts/<int:account_id>/reconcile", methods=["GET", "POST"])
def reconcile(account_id):
    with db.connect() as conn:
        acc = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not acc:
            abort(404)
        if acc["plaid_account_id"]:
            flash("Accounts linked to your bank stay in step with it on their own, so there's nothing to balance.", "error")
            return redirect(url_for("accounts"))
        done = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE account_id = ? AND cleared = 'R'",
                            (account_id,)).fetchone()[0]
        if request.method == "POST":
            stmt = parse_money(request.form.get("balance"))
            try:
                stmt_date = date.fromisoformat(request.form.get("date", "")).isoformat()
            except ValueError:
                stmt_date = date.today().isoformat()
            ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
            rows = conn.execute(
                f"""SELECT id, amount FROM transactions WHERE account_id = ? AND cleared != 'R' AND date <= ?
                    AND id IN ({",".join("?" * len(ids)) or "NULL"})""", [account_id, stmt_date] + ids).fetchall()
            ticked = sum(r["amount"] for r in rows)
            back_to = url_for("reconcile", account_id=account_id, date=stmt_date, balance=request.form.get("balance"))
            if stmt is None:
                flash("Enter the statement's ending balance.", "error")
                return redirect(back_to)
            off = round(done + ticked - stmt, 2)
            if abs(off) > 0.005:
                flash(f"Not balanced yet: the ticked transactions come to {money(done + ticked)}, the statement says "
                      f"{money(stmt)} (off by {money(off)}).", "error")
                return redirect(back_to)
            conn.executemany("UPDATE transactions SET cleared = 'R' WHERE id = ?", [(r["id"],) for r in rows])
            db.set_setting(conn, f"reconciled_{account_id}", json.dumps({"date": stmt_date, "balance": stmt}))
            flash(f"{acc['name']} matches your statement of {stmt_date}. Those {len(rows)} transactions are marked "
                  "reconciled.", "ok")
            return redirect(url_for("accounts"))
        stmt_date = request.args.get("date") or date.today().isoformat()
        rows = conn.execute("SELECT * FROM transactions WHERE account_id = ? AND cleared != 'R' AND date <= ? "
                            "ORDER BY date, id", (account_id, stmt_date)).fetchall()
        try:
            last = json.loads(db.get_setting(conn, f"reconciled_{account_id}") or "null")
        except ValueError:
            last = None
    return render_template("reconcile.html", acc=acc, rows=rows, done=round(done, 2), stmt_date=stmt_date,
                           balance=request.args.get("balance", ""), last=last)


# ---------------------------------------------------------------- an old unencrypted copy from the installed version

OLD_COPY_FILES = ("budget.db", "budget.db-wal", "budget.db-shm")


def old_copies(conn):
    """Folders from an older installed version that still hold an unencrypted copy of the budget (and that you
    haven't said to keep)."""
    try:
        kept = set(json.loads(db.get_setting(conn, "old_copy_kept") or "[]"))
    except ValueError:
        kept = set()
    mine = conn.execute("SELECT COUNT(*), MAX(date) FROM transactions").fetchone()
    out = []
    for d in paths.old_data_dirs():
        if d not in kept and db.is_plaintext(os.path.join(d, "budget.db")) and _copied_already(d, mine):
            out.append(d)
    return out


def _copied_already(folder, mine):
    """Does your budget already have everything the old copy has (at least as many transactions, up to at least the
    same date)? If not, it may hold history you'd lose, so Budget Tool never offers to delete it. Read-only."""
    import sqlite3 as plain_sqlite
    try:
        old = plain_sqlite.connect(f"file:{os.path.join(folder, 'budget.db')}?mode=ro", uri=True)
        try:
            n, last = old.execute("SELECT COUNT(*), MAX(date) FROM transactions").fetchone()
        finally:
            old.close()
    except plain_sqlite.Error:
        return False
    return (mine[0] or 0) >= (n or 0) and (mine[1] or "") >= (last or "")


def _old_copy_files(folder):
    names = [n for n in os.listdir(folder) if n in OLD_COPY_FILES or (n.startswith("budget.db.v") and "-backup-" in n)]
    return [os.path.join(folder, n) for n in sorted(names)]


@app.route("/old-copy", methods=["GET", "POST"])
def old_copy():
    with db.connect() as conn:
        folders = old_copies(conn)
        if request.method == "POST":
            folder = request.form.get("folder", "")
            if folder not in folders:  # only ever a folder this screen listed
                return redirect(url_for("old_copy"))
            if request.form.get("action") == "keep":
                try:
                    kept = json.loads(db.get_setting(conn, "old_copy_kept") or "[]")
                except ValueError:
                    kept = []
                db.set_setting(conn, "old_copy_kept", json.dumps(sorted(set(kept) | {folder})))
                flash("Kept. Budget Tool won't mention it again.", "ok")
            else:
                gone, failed = 0, []
                for f in _old_copy_files(folder):
                    try:
                        os.remove(f)
                        gone += 1
                    except OSError:
                        failed.append(os.path.basename(f))
                if failed:
                    flash(f"Couldn't delete {', '.join(failed)}. Is the old version of Budget Tool still open?", "error")
                else:
                    flash(f"Deleted the old copy ({gone} file{'' if gone == 1 else 's'}).", "ok")
            return redirect(url_for("overview"))
        found = [{"folder": d, "files": [os.path.basename(f) for f in _old_copy_files(d)]} for d in folders]
    return render_template("old_copy.html", found=found)


# ---------------------------------------------------------------- planning categories for a new year
# From December 1 the Overview asks once per launch whether to plan next year's categories. Changes made here
# start on January 1 of that year: earlier years keep their categories exactly as they were.

PLAN_FROM_MONTH = 12      # start asking on December 1
PLAN_NEW_ROWS = 6         # blank "add a category" rows on the planner
SYSTEM_CATEGORIES = (db.TRANSFER, db.BEGINNING)


def year_plan_prompt(conn):
    """The year to ask about on the Overview, or None."""
    today = date.today()
    if today.month < PLAN_FROM_MONTH:
        return None
    year = today.year + 1
    if db.get_setting(conn, f"year_plan_{year}") or session.get(f"year_plan_later_{year}"):
        return None
    return year


@app.post("/categories/plan/later")
def year_plan_later():
    year = int(request.form.get("year") or date.today().year + 1)
    if request.form.get("never") == "1":
        with db.connect() as conn:
            db.set_setting(conn, f"year_plan_{year}", "no changes")
    else:
        session[f"year_plan_later_{year}"] = True  # ask again next time the app is opened
    return redirect(url_for("overview"))


def plan_rows(conn, year):
    """The categories used in `year` that can be planned (the two built-in non-budget ones are left alone)."""
    counts = {r["category_id"]: r["n"] for r in conn.execute(
        "SELECT category_id, COUNT(*) AS n FROM lines WHERE date >= ? GROUP BY category_id", (f"{year}-01-01",))}
    budgeted = {r["category_id"] for r in conn.execute("SELECT DISTINCT category_id FROM budgets WHERE month >= ?", (f"{year}-01",))}
    return [dict(c, later_tx=counts.get(c["id"], 0), budgeted=c["id"] in budgeted)
            for c in reports.categories_in_year(conn, year) if c["name"] not in SYSTEM_CATEGORIES]


def apply_year_plan(conn, year, form):
    """Apply the planner's changes from January 1 of `year`. Returns (summary lines, problem or None).
    Nothing is changed if there's a problem (duplicate or missing names)."""
    kinds = ("income", "expense", "transfer")
    rows = plan_rows(conn, year)
    keep_names, todo = {}, []
    for c in rows:
        keep = form.get(f"keep_{c['id']}") == "1"
        name = (form.get(f"name_{c['id']}") or "").strip() or c["name"]
        kind = form.get(f"kind_{c['id']}") if form.get(f"kind_{c['id']}") in kinds else c["kind"]
        todo.append((c, keep, name, kind))
        if keep:
            keep_names.setdefault(name.lower(), []).append(name)
    added = []
    for i in range(PLAN_NEW_ROWS):
        name = (form.get(f"new_name_{i}") or "").strip()
        if name:
            kind = form.get(f"new_kind_{i}") if form.get(f"new_kind_{i}") in kinds else "expense"
            added.append((name, kind))
            keep_names.setdefault(name.lower(), []).append(name)
    dupes = [v[0] for v in keep_names.values() if len(v) > 1]
    if dupes:
        return [], f"Two categories for {year} would both be called \"{dupes[0]}\". Give them different names."
    for lower in keep_names:
        if lower in (n.lower() for n in SYSTEM_CATEGORIES):
            return [], f"\"{keep_names[lower][0]}\" is a built-in name. Pick another one."

    summary, start = [], f"{year}-01-01"
    for c, keep, name, kind in todo:
        only_from_this_year = c["active_from"] is not None and c["active_from"] >= year
        if not keep:
            if only_from_this_year:
                conn.execute("DELETE FROM categories WHERE id = ?", (c["id"],))  # budgets go; transactions become uncategorized
            else:
                conn.execute("UPDATE categories SET active_until = ? WHERE id = ?", (year - 1, c["id"]))
                conn.execute("DELETE FROM budgets WHERE category_id = ? AND month >= ?", (c["id"], f"{year}-01"))
                conn.execute("UPDATE transactions SET category_id = NULL WHERE category_id = ? AND date >= ?", (c["id"], start))
                conn.execute("UPDATE splits SET category_id = NULL WHERE category_id = ? AND tx_id IN "
                             "(SELECT id FROM transactions WHERE date >= ?)", (c["id"], start))
            summary.append(f"Retired \"{c['name']}\"" + (f" ({c['later_tx']} {year}+ transactions are now uncategorized)" if c["later_tx"] else ""))
        elif name != c["name"] or kind != c["kind"]:
            if only_from_this_year:
                conn.execute("UPDATE categories SET name = ?, kind = ? WHERE id = ?", (name, kind, c["id"]))
            else:
                new_id = conn.execute(
                    "INSERT INTO categories (name, kind, sort, active_from, active_until, replaces) VALUES (?, ?, ?, ?, ?, ?)",
                    (name, kind, c["sort"], year, c["active_until"], c["id"])).lastrowid
                conn.execute("UPDATE categories SET active_until = ? WHERE id = ?", (year - 1, c["id"]))
                conn.execute("UPDATE budgets SET category_id = ? WHERE category_id = ? AND month >= ?", (new_id, c["id"], f"{year}-01"))
                conn.execute("UPDATE transactions SET category_id = ? WHERE category_id = ? AND date >= ?", (new_id, c["id"], start))
                conn.execute("UPDATE splits SET category_id = ? WHERE category_id = ? AND tx_id IN "
                             "(SELECT id FROM transactions WHERE date >= ?)", (new_id, c["id"], start))
            change = []
            if name != c["name"]:
                change.append(f"renamed to \"{name}\"")
            if kind != c["kind"]:
                change.append(f"now {'non-budget' if kind == 'transfer' else kind}")
            summary.append(f"\"{c['name']}\" " + " and ".join(change))
    for name, kind in added:
        conn.execute("INSERT INTO categories (name, kind, sort, active_from) VALUES (?, ?, 500, ?)", (name, kind, year))
        summary.append(f"Added \"{name}\"")
    db.set_setting(conn, f"year_plan_{year}", "planned")
    return summary, None


@app.route("/categories/plan", methods=["GET", "POST"])
def year_plan():
    year = int(request.values.get("year") or date.today().year + 1)
    if year <= date.today().year:
        flash("Planning is for years that haven't started yet. To change categories for every year, edit them on this screen.", "error")
        return redirect(url_for("categories_page"))
    with db.connect() as conn:
        if request.method == "POST":
            summary, problem = apply_year_plan(conn, year, request.form)
            if problem:
                conn.rollback()
                flash(problem, "error")
                return redirect(url_for("year_plan", year=year))
            flash(f"{year} categories saved: " + ("; ".join(summary) if summary else "no changes") + ". "
                  f"{year - 1} and earlier are unchanged.", "ok")
            return redirect(url_for("budget", year=year) if summary else url_for("categories_page"))
        rows = plan_rows(conn, year)
    return render_template("year_plan.html", year=year, rows=rows, new_rows=range(PLAN_NEW_ROWS))


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
            for key in ("plaid_client_id", "plaid_env", "plaid_country_codes"):
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
        data_dir=DATA_DIR, backups=security.backups(), backup_dir=security.BACKUP_DIR,
        lock_choices=LOCK_CHOICES, warning=NO_PASSWORD_WARNING, min_len=security.MIN_PASSWORD,
    )


# ---------------------------------------------------------------- password lock

def safe_next(default=None):
    """Only go back to a page of Budget Tool itself."""
    nxt = request.values.get("next") or ""
    if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt:
        return nxt
    return default or url_for("overview")


def check_password(password, recovery=False):
    """The key if the password (or recovery code) is right. Repeated wrong guesses are slowed down."""
    with PASSWORD_GATE:
        if LOCK["failures"] >= 5:
            time.sleep(min(30, 2 ** (LOCK["failures"] - 5)))
        try:
            key = security.unlock_with_code(password) if recovery else security.unlock(password)
        except security.WrongPassword:
            LOCK["failures"] += 1
            return None
        LOCK["failures"] = 0
        return key


def lock_now():
    db.clear_key()


def confirm_identity():
    """The key, if the form's "current" password is right. Without a password there's nothing to confirm."""
    if not security.has_password():
        return security.open_key()
    return check_password(request.form.get("current", ""))


def finish_unlock(key):
    """The password was right: encrypt anything still unencrypted, open the budget, back it up (once a day), and
    the first time since Budget Tool opened, sync with your bank."""
    db.encrypt_existing(key)
    db.set_key(key)
    LOCK["protected"] = security.has_password()
    db.init()
    with db.connect() as conn:
        combined = duplicates.match(conn)
        if combined:
            unseen = int(db.get_setting(conn, "combined_unseen", "0") or 0)
            db.set_setting(conn, "combined_unseen", str(unseen + combined))
        try:
            LOCK["minutes"] = int(db.get_setting(conn, "lock_after_minutes", "15"))
        except ValueError:
            LOCK["minutes"] = 15
        has_banks = conn.execute("SELECT 1 FROM items").fetchone() is not None
    try:
        security.daily_backup()
    except Exception as e:  # never stop you getting in; Settings shows the backups
        print(f"[backup] failed: {e}")
        flash(f"Today's backup couldn't be made ({e}). Try Back up now on the Settings screen.", "error")
    LOCK["last_active"] = time.time()
    if not LOCK["started"]:
        LOCK["started"] = True
        if has_banks and plaid.configured():
            STARTUP_SYNC["running"] = True
            threading.Thread(target=startup_sync, daemon=True).start()


@app.route("/unlock", methods=["GET", "POST"])
def unlock():
    if not security.is_set_up():
        return redirect(url_for("setup_password"))
    if db.unlocked():
        return redirect(safe_next())
    if request.method == "POST":
        key = check_password(request.form.get("password", ""))
        if key is None:
            flash("That password isn't right.", "error")
            return redirect(url_for("unlock", next=request.form.get("next") or None))
        finish_unlock(key)
        return redirect(safe_next())
    return render_template("lock.html", mode="unlock", next=request.args.get("next", ""))


@app.route("/setup", methods=["GET", "POST"])
def setup_password():
    """First time (or the first time after this update): choose a password. Your data is encrypted with it."""
    if security.is_set_up():
        return redirect(url_for("unlock"))
    existing = os.path.exists(db.DB_PATH)
    if request.method == "POST" and request.form.get("action") == "none":
        if request.form.get("understand") != "1":
            flash("Tick the box to say you understand the risk, or choose a password.", "error")
            return redirect(url_for("setup_password"))
        with PASSWORD_GATE:
            if not security.is_set_up():
                key, _ = security.create(None)
                finish_unlock(key)
        flash("Budget Tool opens without a password. You can add one any time in Settings → Password and lock.", "ok")
        return redirect(url_for("overview"))
    if request.method == "POST":
        pw, confirm = request.form.get("password", ""), request.form.get("confirm", "")
        problem = security.password_problem(pw, confirm)
        if problem:
            flash(problem, "error")
            return redirect(url_for("setup_password"))
        with PASSWORD_GATE:
            if security.is_set_up():
                return redirect(url_for("unlock"))
            key, code = security.create(pw)
            try:
                finish_unlock(key)
            except Exception:
                for f in (security.LOCK_PATH, os.path.join(security.BACKUP_DIR, "lock.json")):
                    if os.path.exists(f):
                        os.remove(f)
                db.clear_key()
                raise
        return render_template("lock.html", mode="code", code=code, first=True)
    return render_template("lock.html", mode="setup", existing=existing, min_len=security.MIN_PASSWORD,
                           warning=NO_PASSWORD_WARNING)


@app.route("/recover", methods=["GET", "POST"])
def recover():
    """Forgot your password: your recovery code lets you set a new one."""
    if not security.is_set_up():
        return redirect(url_for("setup_password"))
    if not security.has_password():
        return redirect(url_for("overview"))
    if request.method == "POST":
        pw, confirm = request.form.get("password", ""), request.form.get("confirm", "")
        problem = security.password_problem(pw, confirm)
        if problem:
            flash(problem, "error")
            return redirect(url_for("recover"))
        key = check_password(request.form.get("code", ""), recovery=True)
        if key is None:
            flash("That recovery code isn't right.", "error")
            return redirect(url_for("recover"))
        security.set_password(key, pw)
        finish_unlock(key)
        flash("New password set. Your recovery code still works; make a new one on the Settings screen if you "
              "think someone else has seen it.", "ok")
        return redirect(url_for("overview"))
    return render_template("lock.html", mode="recover", min_len=security.MIN_PASSWORD)


@app.get("/lock")
def lock():
    if db.unlocked() and not LOCK["protected"]:
        flash("Budget Tool has no password, so it can't lock. Add one below.", "error")
        return redirect(url_for("settings") + "#security")
    if db.unlocked():
        lock_now()
        if request.args.get("idle"):
            flash(f"Budget Tool locked itself after {LOCK['minutes']} minutes without use.", "ok")
    return redirect(url_for("unlock"))


@app.get("/api/still-here")
def api_still_here():
    """The window says you're using it (typing, scrolling), so the automatic lock waits."""
    return jsonify(ok=True)


@app.post("/settings/password")
def settings_password():
    pw, confirm = request.form.get("password", ""), request.form.get("confirm", "")
    if not security.has_password():  # adding a password to a budget that had none
        problem = security.password_problem(pw, confirm)
        if problem:
            flash(problem, "error")
            return redirect(url_for("settings") + "#security")
        code = security.add_password(security.open_key(), pw)
        LOCK["protected"], LOCK["last_active"] = True, time.time()
        return render_template("lock.html", mode="code", code=code, first=False, added=True)
    key = check_password(request.form.get("current", ""))
    if key is None:
        flash("Your current password isn't right, so nothing was changed.", "error")
    elif security.password_problem(pw, confirm):
        flash(security.password_problem(pw, confirm), "error")
    else:
        security.set_password(key, pw)
        flash("Password changed. Use the new one from now on (your backups open with it too).", "ok")
    return redirect(url_for("settings") + "#security")


@app.post("/settings/remove-password")
def settings_remove_password():
    if request.form.get("understand") != "1":
        flash("Tick the box to say you understand the risk. Your password was kept.", "error")
        return redirect(url_for("settings") + "#security")
    key = check_password(request.form.get("current", ""))
    if key is None:
        flash("That password isn't right, so it was kept.", "error")
        return redirect(url_for("settings") + "#security")
    security.remove_password(key)
    LOCK["protected"] = False
    flash("Password removed. Budget Tool now opens without one, and your budget isn't protected.", "ok")
    return redirect(url_for("settings") + "#security")


@app.post("/settings/recovery-code")
def settings_recovery_code():
    key = check_password(request.form.get("current", ""))
    if key is None:
        flash("That password isn't right, so your recovery code wasn't changed.", "error")
        return redirect(url_for("settings") + "#security")
    return render_template("lock.html", mode="code", code=security.new_recovery_code(key), first=False)


@app.post("/settings/lock-after")
def settings_lock_after():
    raw = request.form.get("minutes", "15")
    minutes = int(raw) if raw.isdigit() and int(raw) in LOCK_CHOICES else 15
    with db.connect() as conn:
        db.set_setting(conn, "lock_after_minutes", str(minutes))
    LOCK["minutes"] = minutes
    flash("Saved.", "ok")
    return redirect(url_for("settings") + "#security")


@app.post("/backups/now")
def backups_now():
    try:
        name = security.make_backup("manual")
        flash(f"Backed up ({name}). Copies you make yourself are kept until you delete them.", "ok")
    except Exception as e:
        flash(f"Couldn't back up: {e}", "error")
    return redirect(url_for("settings") + "#backups")


@app.post("/backups/open")
def backups_open():
    os.makedirs(security.BACKUP_DIR, exist_ok=True)
    try:
        os.startfile(security.BACKUP_DIR)
    except OSError:
        flash(f"Couldn't open the folder. It's {security.BACKUP_DIR}", "error")
    return redirect(url_for("settings") + "#backups")


@app.post("/backups/restore")
def backups_restore():
    if STARTUP_SYNC["running"]:
        flash("Wait until Budget Tool has finished syncing with your bank, then try again.", "error")
        return redirect(url_for("settings") + "#backups")
    if confirm_identity() is None:
        flash("That password isn't right, so nothing was restored.", "error")
        return redirect(url_for("settings") + "#backups")
    name = request.form.get("name", "")
    try:
        before = security.restore(name)
    except Exception as e:
        flash(f"Couldn't restore that backup: {e}", "error")
        return redirect(url_for("settings") + "#backups")
    flash(f"Restored {name}. Your budget as it was just before is saved as {before}, in case you want it back.", "ok")
    return redirect(url_for("overview"))


# ---------------------------------------------------------------- updates

@app.post("/update/check")
def update_check():
    st = updater.check(force=True)
    if st["error"]:
        flash(st["error"], "error")
    elif st["available"]:
        flash(f"An update is available: {st['latest']}.", "ok")
    elif st["from_source"]:
        flash("This copy runs from source, so it doesn't update itself. Use git pull.", "ok")
    else:
        flash(f"You're up to date ({st['current']}).", "ok")
    return back("settings")


@app.post("/update/install")
def update_install():
    """Hand over to "Update Budget Tool.exe": it waits for Budget Tool to close, installs, and reopens it."""
    try:
        updater.launch_updater()
    except Exception as e:
        flash(f"Couldn't start the updater: {e}", "error")
        return back("settings")
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
           + (" (more may appear the next time you open Budget Tool)." if result["added"] == 0 else ".")
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


# ---------------------------------------------------------------- background server, bank sync on open

def startup_sync():
    """Bring every connected bank up to date once, as Budget Tool opens. The window shows "Syncing with your
    bank" meanwhile, and reloads when it's done if anything changed. Problems show on the Accounts screen."""
    try:
        totals, errors = plaid.sync_all()
        STARTUP_SYNC["changed"] = totals["added"] + totals["modified"] + totals["removed"]
        print(f"[sync] {totals}" + (f" errors: {errors}" if errors else ""))
    except Exception as e:
        print(f"[sync] failed: {e}")
    finally:
        STARTUP_SYNC["running"] = False


@app.get("/api/sync-status")
def api_sync_status():
    return jsonify(running=STARTUP_SYNC["running"], changed=STARTUP_SYNC["changed"])


def update_check_loop():
    while True:
        try:
            updater.check()  # at most every 6 hours; shows a banner when there's a new version
        except Exception as e:
            print(f"[update-check] failed: {e}")
        time.sleep(6 * 3600)


def start_server():
    """Start the local server on a free port in a background thread. Returns the window's start URL."""
    global BASE_URL
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", 0, app, threaded=True)
    BASE_URL = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # Nothing is read from your budget until you unlock it; the bank sync starts then (finish_unlock).
    threading.Thread(target=update_check_loop, daemon=True).start()
    return f"{BASE_URL}/?key={LAUNCH_KEY}"


if __name__ == "__main__":
    # Developer mode: open in your browser instead of the app window.
    start_url = start_server()
    print("Budget Tool (developer mode):", start_url)
    webbrowser.open(start_url)
    threading.Event().wait()
