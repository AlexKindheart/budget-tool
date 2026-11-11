"""Budget Tool screens and routes (Flask). Shown inside the desktop window by budget_tool.py.

The server only listens on 127.0.0.1 and every request needs the per-launch key
(LAUNCH_KEY), so other programs and websites can't read your data.
"""
import os
import secrets
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta, timezone

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,
                   session, url_for)

import db
import plaid_client as plaid
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


def bump_data_version():
    DATA_VERSION[0] += 1


# ---------------------------------------------------------------- helpers

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


@app.context_processor
def inject_globals():
    return {"csrf_token": csrf_token, "plaid_env": plaid.env(), "plaid_ready": plaid.configured(),
            "in_browser": request.args.get("browser") == "1"}


@app.template_filter("money")
def money(value):
    value = value or 0
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


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


def month_param():
    """Return (YYYY-MM, prev, next, label) for ?month=, defaulting to this month."""
    raw = request.args.get("month", "")
    try:
        first = datetime.strptime(raw, "%Y-%m").date()
    except ValueError:
        first = date.today().replace(day=1)
    prev = (first - timedelta(days=1)).replace(day=1)
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first.strftime("%Y-%m"), prev.strftime("%Y-%m"), nxt.strftime("%Y-%m"), first.strftime("%B %Y")


def run_sync_and_flash():
    if not plaid.configured():
        flash("Add your Plaid keys on the Settings screen first.", "error")
        return
    totals, errors = plaid.sync_all()
    flash(f"Synced: {totals['added']} new, {totals['modified']} updated, {totals['removed']} removed.", "ok")
    for err in errors:
        flash(err, "error")


# ---------------------------------------------------------------- pages

@app.route("/")
def dashboard():
    month, prev, nxt, label = month_param()
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT c.id, c.name, c.kind, c.budget, COALESCE(SUM(t.amount), 0) AS total
               FROM categories c
               LEFT JOIN transactions t ON t.category_id = c.id AND substr(t.date, 1, 7) = ?
               GROUP BY c.id ORDER BY c.name""",
            (month,),
        ).fetchall()
        uncategorized = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE category_id IS NULL AND substr(date, 1, 7) = ?",
            (month,),
        ).fetchone()[0]
        accounts = conn.execute(
            """SELECT a.*, i.institution_name FROM accounts a JOIN items i ON i.item_id = a.item_id
               ORDER BY i.institution_name, a.name"""
        ).fetchall()
        items = conn.execute("SELECT * FROM items").fetchall()
        recent = conn.execute(
            """SELECT t.*, c.name AS category FROM transactions t
               LEFT JOIN categories c ON c.id = t.category_id
               ORDER BY t.date DESC, t.rowid DESC LIMIT 8"""
        ).fetchall()

    expenses = [r for r in rows if r["kind"] == "expense" and (r["budget"] or r["total"])]
    expenses.sort(key=lambda r: -r["total"])
    spent = sum(r["total"] for r in rows if r["kind"] == "expense") + uncategorized
    income = -sum(r["total"] for r in rows if r["kind"] == "income")
    budget_total = sum(r["budget"] for r in rows if r["kind"] == "expense")
    last_synced = max((i["last_synced"] for i in items if i["last_synced"]), default=None)

    return render_template(
        "dashboard.html", month=month, prev=prev, nxt=nxt, label=label,
        expenses=expenses, uncategorized=uncategorized, spent=spent, income=income,
        budget_total=budget_total, accounts=accounts, items=items, recent=recent,
        last_synced=last_synced,
    )


@app.route("/transactions")
def transactions():
    month, prev, nxt, label = month_param()
    cat = request.args.get("category", "")
    q = request.args.get("q", "").strip()
    sql = """SELECT t.*, c.name AS category, a.name AS account, a.mask
             FROM transactions t
             LEFT JOIN categories c ON c.id = t.category_id
             LEFT JOIN accounts a ON a.account_id = t.account_id
             WHERE substr(t.date, 1, 7) = ?"""
    params = [month]
    if cat == "none":
        sql += " AND t.category_id IS NULL"
    elif cat.isdigit():
        sql += " AND t.category_id = ?"
        params.append(int(cat))
    if q:
        sql += " AND (t.name LIKE ? ESCAPE '!' OR t.merchant LIKE ? ESCAPE '!')"
        like = "%" + q.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        params += [like, like]
    sql += " ORDER BY t.date DESC, t.rowid DESC LIMIT 2000"
    with db.connect() as conn:
        txs = conn.execute(sql, params).fetchall()
        categories = conn.execute("SELECT * FROM categories ORDER BY name").fetchall()
    return render_template(
        "transactions.html", txs=txs, categories=categories, month=month, prev=prev,
        nxt=nxt, label=label, cat=cat, q=q,
    )


@app.post("/transactions/<tx_id>/category")
def set_category(tx_id):
    cat_raw = request.form.get("category_id", "")
    category_id = int(cat_raw) if cat_raw.isdigit() else None
    with db.connect() as conn:
        tx = conn.execute("SELECT * FROM transactions WHERE transaction_id = ?", (tx_id,)).fetchone()
        if not tx:
            abort(404)
        conn.execute(
            "UPDATE transactions SET category_id = ?, category_locked = 1 WHERE transaction_id = ?",
            (category_id, tx_id),
        )
        if request.form.get("apply_rule") and category_id:
            key = (tx["merchant"] or tx["name"] or "").strip().lower()
            if key:
                conn.execute(
                    "INSERT INTO rules (match, category_id) VALUES (?, ?) "
                    "ON CONFLICT(match) DO UPDATE SET category_id = excluded.category_id",
                    (key, category_id),
                )
                n = conn.execute(
                    """UPDATE transactions SET category_id = ?
                       WHERE category_locked = 0
                         AND lower(trim(COALESCE(NULLIF(merchant, ''), name, ''))) = ?""",
                    (category_id, key),
                ).rowcount
                flash(f"Rule saved: \"{key}\" is always this category ({n} other transactions updated).", "ok")
    return redirect(request.form.get("next") or url_for("transactions"))


@app.route("/budgets", methods=["GET", "POST"])
def budgets():
    if request.method == "POST":
        action = request.form.get("action")
        with db.connect() as conn:
            if action == "save":
                for c in conn.execute("SELECT id FROM categories").fetchall():
                    cid = c["id"]
                    name = request.form.get(f"name_{cid}", "").strip()
                    kind = request.form.get(f"kind_{cid}", "expense")
                    try:
                        budget = max(0.0, float(request.form.get(f"budget_{cid}", "0") or 0))
                    except ValueError:
                        budget = 0.0
                    if kind not in ("expense", "income", "transfer"):
                        kind = "expense"
                    if name:
                        try:
                            conn.execute("UPDATE categories SET name=?, kind=?, budget=? WHERE id=?",
                                         (name, kind, budget, cid))
                        except Exception:
                            flash(f"There's already a category called \"{name}\".", "error")
                flash("Budgets saved.", "ok")
            elif action == "add":
                name = request.form.get("name", "").strip()
                if name:
                    try:
                        conn.execute("INSERT INTO categories (name, kind) VALUES (?, ?)",
                                     (name, request.form.get("kind", "expense")))
                        flash(f"Added \"{name}\".", "ok")
                    except Exception:
                        flash(f"There's already a category called \"{name}\".", "error")
            elif action == "delete":
                conn.execute("DELETE FROM categories WHERE id = ?", (int(request.form.get("id", 0)),))
                flash("Category deleted. Its transactions are now uncategorized.", "ok")
            elif action == "delete_rule":
                conn.execute("DELETE FROM rules WHERE id = ?", (int(request.form.get("id", 0)),))
                flash("Rule removed.", "ok")
        return redirect(url_for("budgets"))

    with db.connect() as conn:
        categories = conn.execute(
            "SELECT * FROM categories ORDER BY CASE kind WHEN 'income' THEN 0 WHEN 'expense' THEN 1 ELSE 2 END, name"
        ).fetchall()
        rules = conn.execute(
            "SELECT r.*, c.name AS category FROM rules r JOIN categories c ON c.id = r.category_id ORDER BY r.match"
        ).fetchall()
    return render_template("budgets.html", categories=categories, rules=rules)


@app.route("/connections")
def connections():
    with db.connect() as conn:
        items = conn.execute("SELECT * FROM items ORDER BY institution_name").fetchall()
        accounts = conn.execute("SELECT * FROM accounts ORDER BY name").fetchall()
    by_item = {}
    for a in accounts:
        by_item.setdefault(a["item_id"], []).append(a)
    return render_template("connections.html", items=items, by_item=by_item)


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


@app.post("/sync")
def sync():
    run_sync_and_flash()
    return redirect(request.form.get("next") or url_for("dashboard"))


@app.post("/connections/<item_id>/remove")
def remove_connection(item_id):
    plaid.remove_item(item_id)
    flash("Bank disconnected and its transactions deleted.", "ok")
    return redirect(url_for("connections"))


# ---------------------------------------------------------------- Plaid Link
# Plaid Link (the bank sign-in screen) runs in your normal web browser, where bank security
# redirects work reliably. The app window opens it and waits for DATA_VERSION to change.

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


WINDOW_MESSAGES = []  # messages from the browser sign-in, shown in the app window next time it loads


@app.before_request
def deliver_window_messages():
    if request.method == "GET" and request.args.get("browser") != "1" and not request.path.startswith(("/api/", "/static/")):
        while WINDOW_MESSAGES:
            flash(*WINDOW_MESSAGES.pop(0))


@app.post("/api/exchange")
def api_exchange():
    body = request.get_json(silent=True) or {}
    try:
        item_id = plaid.exchange_public_token(body.get("public_token", ""), body.get("institution") or "Bank")
        result = plaid.sync_item(item_id)
    except Exception as e:
        return jsonify(error=str(e)), 502
    msg = (f"Bank connected. Pulled {result['added']} transactions"
           + (" (more may appear in a few minutes - click Sync now)." if result["added"] == 0 else "."))
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
