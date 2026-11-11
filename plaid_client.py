"""Small Plaid API client (plain HTTPS + JSON) and the transaction sync logic."""
import os
from datetime import datetime, timezone

import requests

import db

PLAID_HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "production": "https://production.plaid.com",
}


class PlaidError(Exception):
    def __init__(self, data):
        self.data = data or {}
        self.code = self.data.get("error_code", "UNKNOWN")
        msg = self.data.get("display_message") or self.data.get("error_message") or str(data)
        super().__init__(f"{self.code}: {msg}")


CONFIG_DEFAULTS = {
    "plaid_client_id": "",
    "plaid_secret": "",
    "plaid_env": "sandbox",
    "plaid_country_codes": "US",
    "sync_interval_hours": "6",
}


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
    payload = {
        "client_id": cfg("plaid_client_id"),
        "secret": cfg("plaid_secret"),
        **body,
    }
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


def remove_item(item_id):
    with db.connect() as conn:
        row = conn.execute("SELECT access_token FROM items WHERE item_id = ?", (item_id,)).fetchone()
        if not row:
            return
        try:
            call("/item/remove", {"access_token": row["access_token"]})
        except PlaidError:
            pass  # remove locally even if Plaid already forgot it
        account_ids = [r["account_id"] for r in conn.execute(
            "SELECT account_id FROM accounts WHERE item_id = ?", (item_id,))]
        conn.executemany("DELETE FROM transactions WHERE account_id = ?", [(a,) for a in account_ids])
        conn.execute("DELETE FROM items WHERE item_id = ?", (item_id,))


def _refresh_accounts(conn, item_id, access_token):
    data = call("/accounts/get", {"access_token": access_token})
    for a in data["accounts"]:
        bal = a.get("balances") or {}
        conn.execute(
            """INSERT INTO accounts (account_id, item_id, name, mask, type, subtype, current, available, currency)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(account_id) DO UPDATE SET
                 name = excluded.name, mask = excluded.mask, type = excluded.type,
                 subtype = excluded.subtype, current = excluded.current,
                 available = excluded.available, currency = excluded.currency""",
            (a["account_id"], item_id, a.get("official_name") or a.get("name"), a.get("mask"),
             a.get("type"), a.get("subtype"), bal.get("current"), bal.get("available"),
             bal.get("iso_currency_code") or bal.get("unofficial_currency_code")),
        )


def _merchant_key(t):
    return (t.get("merchant_name") or t.get("name") or "").strip().lower()


def _upsert_transaction(conn, t):
    pfc = t.get("personal_finance_category") or {}
    primary, detailed = pfc.get("primary"), pfc.get("detailed")
    existing = conn.execute(
        "SELECT category_locked FROM transactions WHERE transaction_id = ?", (t["transaction_id"],)
    ).fetchone()
    locked = existing and existing["category_locked"]
    params = (t["account_id"], t["date"], t.get("name"), t.get("merchant_name"), t["amount"],
              1 if t.get("pending") else 0, primary, detailed)
    if existing and locked:
        conn.execute(
            """UPDATE transactions SET account_id=?, date=?, name=?, merchant=?, amount=?, pending=?,
               pfc_primary=?, pfc_detailed=? WHERE transaction_id=?""",
            params + (t["transaction_id"],),
        )
        return
    category_id = db.category_for(conn, _merchant_key(t), primary, detailed)
    conn.execute(
        """INSERT INTO transactions (account_id, date, name, merchant, amount, pending,
                                     pfc_primary, pfc_detailed, category_id, transaction_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(transaction_id) DO UPDATE SET
             account_id=excluded.account_id, date=excluded.date, name=excluded.name,
             merchant=excluded.merchant, amount=excluded.amount, pending=excluded.pending,
             pfc_primary=excluded.pfc_primary, pfc_detailed=excluded.pfc_detailed,
             category_id=excluded.category_id""",
        params + (category_id, t["transaction_id"]),
    )


def sync_item(item_id):
    """Pull all new/changed/removed transactions for one bank connection. Returns counts."""
    with db.connect() as conn:
        item = conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
    if not item:
        return {"added": 0, "modified": 0, "removed": 0}

    access_token = item["access_token"]
    start_cursor = item["cursor"]
    try:
        # Page through everything first, then write it all in one go. If the data changes
        # mid-pagination Plaid asks us to restart from the original cursor.
        for _attempt in range(3):
            cursor, added, modified, removed = start_cursor, [], [], []
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
            raise RuntimeError("Plaid kept changing data during sync; try again in a minute.")

        with db.connect() as conn:
            _refresh_accounts(conn, item_id, access_token)
            for t in added + modified:
                _upsert_transaction(conn, t)
            conn.executemany(
                "DELETE FROM transactions WHERE transaction_id = ?",
                [(r["transaction_id"],) for r in removed],
            )
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
