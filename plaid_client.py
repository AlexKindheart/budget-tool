"""Small Plaid API client (plain HTTPS + JSON) and the sync that fills the register."""
import os
from datetime import date, datetime, timedelta, timezone

import requests

import db

PLAID_HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "production": "https://production.plaid.com",
}

CONFIG_DEFAULTS = {
    "plaid_client_id": "",
    "plaid_secret": "",
    "plaid_env": "sandbox",
    "plaid_country_codes": "US",
}


class PlaidError(Exception):
    def __init__(self, data):
        self.data = data or {}
        self.code = self.data.get("error_code", "UNKNOWN")
        msg = self.data.get("display_message") or self.data.get("error_message") or str(data)
        super().__init__(f"{self.code}: {msg}")


def cfg(key):
    """Setting saved on the in-app Settings screen, else environment variable, else default."""
    with db.connect() as conn:
        value = db.get_setting(conn, key)
    if value in (None, ""):
        value = os.environ.get(key.upper(), CONFIG_DEFAULTS.get(key, ""))
    return str(value).strip()


def env():
    return cfg("plaid_env").lower()


def configured():
    return bool(cfg("plaid_client_id") and cfg("plaid_secret"))


def call(path, body):
    host = PLAID_HOSTS.get(env())
    if not host:
        raise RuntimeError("Plaid environment must be 'sandbox' or 'production'.")
    payload = {"client_id": cfg("plaid_client_id"), "secret": cfg("plaid_secret"), **body}
    resp = requests.post(host + path, json=payload, timeout=60)
    data = resp.json() if resp.content else {}
    if resp.status_code != 200:
        raise PlaidError(data)
    return data


def create_link_token(access_token=None):
    """Link token for connecting a new bank, or for update mode when access_token is given."""
    body = {
        "user": {"client_user_id": "budget-tool-local-user"},
        "client_name": "Budget Tool",
        "country_codes": [c.strip().upper() for c in cfg("plaid_country_codes").split(",") if c.strip()] or ["US"],
        "language": "en",
    }
    if access_token:
        body["access_token"] = access_token
    else:
        body["products"] = ["transactions"]
        body["transactions"] = {"days_requested": 730}
    return call("/link/token/create", body)["link_token"]


def exchange_public_token(public_token, institution_name):
    data = call("/item/public_token/exchange", {"public_token": public_token})
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO items (item_id, access_token, institution_name) VALUES (?, ?, ?) "
            "ON CONFLICT(item_id) DO UPDATE SET access_token = excluded.access_token, error = NULL",
            (data["item_id"], data["access_token"], institution_name),
        )
    return data["item_id"]


def remove_item(item_id, delete_transactions):
    """Disconnect a bank. Its accounts stay in the app as manual accounts unless delete_transactions."""
    with db.connect() as conn:
        row = conn.execute("SELECT access_token FROM items WHERE item_id = ?", (item_id,)).fetchone()
        if not row:
            return
        try:
            call("/item/remove", {"access_token": row["access_token"]})
        except PlaidError:
            pass  # remove locally even if Plaid already forgot it
        if delete_transactions:
            conn.execute("DELETE FROM accounts WHERE item_id = ?", (item_id,))
        else:
            conn.execute(
                "UPDATE accounts SET plaid_account_id = NULL, item_id = NULL, bank_current = NULL, "
                "bank_available = NULL, auto_opening = 0 WHERE item_id = ?", (item_id,))
            conn.execute(
                "UPDATE transactions SET plaid_id = NULL, is_opening = 0 WHERE account_id IN "
                "(SELECT id FROM accounts WHERE item_id IS NULL) AND plaid_id IS NOT NULL")
        conn.execute("DELETE FROM items WHERE item_id = ?", (item_id,))


# ---------------------------------------------------------------- sync

def _account_kind(a):
    t, st = a.get("type"), (a.get("subtype") or "")
    if t == "credit":
        return "credit"
    if t == "loan":
        return "loan"
    if t == "depository":
        return "savings" if st in ("savings", "money market", "cd", "hsa") else "checking"
    return "other"


def _unique_name(conn, base):
    name, n = base, 2
    while conn.execute("SELECT 1 FROM accounts WHERE name = ?", (name,)).fetchone():
        name, n = f"{base} ({n})", n + 1
    return name


def _refresh_accounts(conn, item, accounts):
    """Create/update app accounts for the bank's accounts. Returns {plaid_account_id: row}."""
    out = {}
    for a in accounts:
        bal = a.get("balances") or {}
        row = conn.execute("SELECT * FROM accounts WHERE plaid_account_id = ?", (a["account_id"],)).fetchone()
        if not row:
            base = f"{item['institution_name'] or 'Bank'} {a.get('name') or a.get('official_name') or 'Account'}"
            if a.get("mask"):
                base += f" {a['mask']}"
            conn.execute(
                "INSERT INTO accounts (name, kind, plaid_account_id, item_id, sort) VALUES (?, ?, ?, ?, 100)",
                (_unique_name(conn, base), _account_kind(a), a["account_id"], item["item_id"]),
            )
        conn.execute(
            "UPDATE accounts SET mask = ?, bank_current = ?, bank_available = ?, item_id = ? WHERE plaid_account_id = ?",
            (a.get("mask"), bal.get("current"), bal.get("available"), item["item_id"], a["account_id"]),
        )
        out[a["account_id"]] = conn.execute(
            "SELECT * FROM accounts WHERE plaid_account_id = ?", (a["account_id"],)).fetchone()
    return out


def _upsert_transaction(conn, t, account):
    if account["sync_from"] and t["date"] < account["sync_from"]:
        return
    amount = -float(t["amount"])  # Plaid: + = money out. Register: + = money in.
    bank_name = t.get("name") or ""
    merchant = t.get("merchant_name") or ""
    pfc = t.get("personal_finance_category") or {}
    payee = merchant or bank_name
    category_id = None  # you categorize every transaction yourself; new ones arrive Uncategorized
    cleared = "" if t.get("pending") else "c"

    existing = conn.execute("SELECT * FROM transactions WHERE plaid_id = ?", (t["transaction_id"],)).fetchone()
    # A pending charge that just posted comes back with a new id: carry over your edits.
    if not existing and t.get("pending_transaction_id"):
        existing_pending = conn.execute(
            "SELECT * FROM transactions WHERE plaid_id = ?", (t["pending_transaction_id"],)).fetchone()
        if existing_pending:
            conn.execute("UPDATE transactions SET plaid_id = ? WHERE id = ?", (t["transaction_id"], existing_pending["id"]))
            existing = conn.execute("SELECT * FROM transactions WHERE id = ?", (existing_pending["id"],)).fetchone()

    if existing:
        if existing["payee_locked"]:
            payee = existing["payee"]
        category_id = existing["category_id"]  # a bank update never changes your category
        if existing["cleared"] == "R":
            cleared = "R"
        conn.execute(
            """UPDATE transactions SET account_id=?, date=?, payee=?, bank_name=?, amount=?, cleared=?,
               category_id=?, pfc_primary=?, pfc_detailed=? WHERE id=?""",
            (account["id"], t["date"], payee, bank_name, amount, cleared, category_id,
             pfc.get("primary"), pfc.get("detailed"), existing["id"]),
        )
    else:
        conn.execute(
            """INSERT INTO transactions (plaid_id, account_id, date, payee, bank_name, amount, cleared,
                                         category_id, pfc_primary, pfc_detailed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (t["transaction_id"], account["id"], t["date"], payee, bank_name, amount, cleared, category_id,
             pfc.get("primary"), pfc.get("detailed")),
        )


def bank_balance_in_register_sign(account):
    """The bank's balance with register sign (credit card/loan debt is negative)."""
    cur = account["bank_current"]
    if cur is None:
        return None
    return -cur if account["kind"] in ("credit", "loan") else cur


def refresh_opening_balance(conn, account_id):
    """Keep the automatic [Beginning Balance] entry sized so the register's cleared balance
    equals the bank's current balance."""
    acc = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    target = bank_balance_in_register_sign(acc) if acc else None
    if not acc or not acc["auto_opening"] or target is None:
        return
    others = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s, MIN(date) AS d FROM transactions "
        "WHERE account_id = ? AND is_opening = 0 AND cleared != ''", (account_id,)).fetchone()
    first = conn.execute(
        "SELECT MIN(date) FROM transactions WHERE account_id = ? AND is_opening = 0", (account_id,)).fetchone()[0]
    open_date = first or date.today().isoformat()
    amount = round(target - others["s"], 2)
    cat = db.ensure_category(conn, db.BEGINNING, "transfer")
    row = conn.execute("SELECT id FROM transactions WHERE account_id = ? AND is_opening = 1", (account_id,)).fetchone()
    if row:
        conn.execute("UPDATE transactions SET date = ?, amount = ? WHERE id = ?", (open_date, amount, row["id"]))
    else:
        conn.execute(
            """INSERT INTO transactions (account_id, date, payee, amount, cleared, category_id, is_opening,
                                         category_locked, payee_locked)
               VALUES (?, ?, ?, ?, 'R', ?, 1, 1, 1)""",
            (account_id, open_date, db.BEGINNING, amount, cat),
        )


def sync_item(item_id):
    """Pull all new/changed/removed transactions for one bank connection. Returns counts."""
    with db.connect() as conn:
        item = conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
    if not item:
        return {"added": 0, "modified": 0, "removed": 0}

    access_token = item["access_token"]
    try:
        # Page through everything first, then write it in one go. If the bank's data changes
        # mid-way, Plaid asks us to restart from the original cursor.
        for _attempt in range(3):
            cursor, added, modified, removed = item["cursor"], [], [], []
            try:
                while True:
                    body = {"access_token": access_token, "count": 500}
                    if cursor:
                        body["cursor"] = cursor
                    page = call("/transactions/sync", body)
                    added += page.get("added", [])
                    modified += page.get("modified", [])
                    removed += page.get("removed", [])
                    cursor = page.get("next_cursor")
                    if not page.get("has_more"):
                        break
                break
            except PlaidError as e:
                if e.code != "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
                    raise
        else:
            raise RuntimeError("The bank kept changing data during sync; try again in a minute.")

        accounts_data = call("/accounts/get", {"access_token": access_token})["accounts"]
        with db.connect() as conn:
            accounts = _refresh_accounts(conn, item, accounts_data)
            for t in sorted(added + modified, key=lambda x: x.get("pending", False)):
                acc = accounts.get(t["account_id"])
                if acc:
                    _upsert_transaction(conn, t, acc)
            conn.executemany("DELETE FROM transactions WHERE plaid_id = ?",
                             [(r["transaction_id"],) for r in removed])
            for acc in accounts.values():
                refresh_opening_balance(conn, acc["id"])
            conn.execute(
                "UPDATE items SET cursor = ?, last_synced = ?, error = NULL WHERE item_id = ?",
                (cursor, datetime.now(timezone.utc).isoformat(timespec="seconds"), item_id),
            )
        return {"added": len(added), "modified": len(modified), "removed": len(removed)}
    except PlaidError as e:
        with db.connect() as conn:
            conn.execute("UPDATE items SET error = ? WHERE item_id = ?", (str(e), item_id))
        raise


def sync_all():
    """Sync every connected bank. Returns (totals, list of error strings)."""
    with db.connect() as conn:
        items = conn.execute("SELECT item_id, institution_name FROM items").fetchall()
    totals = {"added": 0, "modified": 0, "removed": 0}
    errors = []
    for it in items:
        try:
            r = sync_item(it["item_id"])
            for k in totals:
                totals[k] += r[k]
        except Exception as e:  # keep going with the other banks
            errors.append(f"{it['institution_name'] or it['item_id']}: {e}")
    return totals, errors


def link_to_existing(bank_account_id, target_account_id):
    """Merge a bank-synced account into an account you already track (e.g. one imported from
    your spreadsheet). Bank transactions continue from the day after your last entry there."""
    with db.connect() as conn:
        src = conn.execute("SELECT * FROM accounts WHERE id = ?", (bank_account_id,)).fetchone()
        dst = conn.execute("SELECT * FROM accounts WHERE id = ?", (target_account_id,)).fetchone()
        if not src or not dst or not src["plaid_account_id"] or dst["plaid_account_id"] or src["id"] == dst["id"]:
            raise ValueError("Pick a bank account and a different account that isn't linked to a bank.")
        last = conn.execute(
            "SELECT MAX(date) FROM transactions WHERE account_id = ?", (dst["id"],)).fetchone()[0]
        sync_from = (date.fromisoformat(last) + timedelta(days=1)).isoformat() if last else None
        # Keep bank entries after your last manual entry; drop the earlier ones and the auto opening balance.
        conn.execute("DELETE FROM transactions WHERE account_id = ? AND (is_opening = 1 OR date < ?)",
                     (src["id"], sync_from or "0000"))
        conn.execute("UPDATE transactions SET account_id = ? WHERE account_id = ?", (dst["id"], src["id"]))
        conn.execute("DELETE FROM accounts WHERE id = ?", (src["id"],))
        conn.execute(
            """UPDATE accounts SET plaid_account_id = ?, item_id = ?, mask = ?, bank_current = ?,
               bank_available = ?, sync_from = ?, auto_opening = 0, kind = ? WHERE id = ?""",
            (src["plaid_account_id"], src["item_id"], src["mask"], src["bank_current"], src["bank_available"],
             sync_from, src["kind"], dst["id"]),
        )
        return sync_from
