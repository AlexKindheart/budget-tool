"""Money moved between your own accounts: it's neither income nor spending, so it must stay out of the numbers.

- Matching: a payment in one account and a deposit of the same amount in another, within MATCH_DAYS of each other,
  are paired (transactions.transfer_pair points at the other side) and both get the [Transfer] category, which
  reports leave out. Only transactions that are Uncategorized or already [Transfer] are matched; a category you
  picked is never changed. Pending bank charges wait until they post. "Not a transfer" undoes a pair for good.
- Debt payments: paying a credit card or loan account from another account is matched the same way, but the paying
  side gets the Debt category (it counts as debt paid, i.e. spending) and only the card/loan side gets [Transfer].
- Suggestions: Plaid marks transfers and credit card payments. When the other side isn't in Budget Tool, those
  get a one-click "[Transfer]?" suggestion (a card payment going out: "Debt?") instead of being categorized for you.
- Checking: [Transfer]s with no other side and transfer-looking transactions that are still Uncategorized are listed
  for you to look at (To do on the Overview, "Transfers to check" on the Register). "Other side isn't in Budget
  Tool" marks a one-sided transfer as fine.

transactions.transfer_state: NULL (nothing decided), 'no' (you said it isn't a transfer), 'outside' (a transfer
whose other side is an account you don't track here).
"""
import db

MATCH_DAYS = 5
TRANSFER_PFC_PRIMARY = ("TRANSFER_IN", "TRANSFER_OUT")
CARD_PAYMENT_PFC = "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"
DEBT = "Debt"
DEBT_ACCOUNTS = ("credit", "loan")


def transfer_category(conn):
    return db.ensure_category(conn, db.TRANSFER, "transfer")


def debt_ids(conn):
    """Every expense category called Debt (there can be one per year range)."""
    return {r["id"] for r in conn.execute("SELECT id FROM categories WHERE lower(name) = lower(?) AND kind = 'expense'",
                                          (DEBT,))}


def debt_category(conn, year):
    """The Debt category used in that year (made if you don't have one)."""
    for c in conn.execute("SELECT * FROM categories WHERE lower(name) = lower(?) AND kind = 'expense' ORDER BY id",
                          (DEBT,)):
        if db.active_in(c, year):
            return c["id"]
    return conn.execute("INSERT INTO categories (name, kind, sort, active_from) VALUES (?, 'expense', 999, ?)",
                        (DEBT, year if debt_ids(conn) else None)).lastrowid


def _kinds(conn):
    return {r["id"]: r["kind"] for r in conn.execute("SELECT id, kind FROM accounts")}


def is_debt_payment(kinds, out_account, dep_account):
    """Money going from an everyday account into a credit card or loan account."""
    return kinds.get(dep_account) in DEBT_ACCOUNTS and kinds.get(out_account) not in DEBT_ACCOUNTS


def card_payment_out(t):
    """A credit card payment leaving an everyday account (your bank says so)."""
    return t["pfc_detailed"] == CARD_PAYMENT_PFC and t["amount"] < 0


def looks_like_transfer(t):
    """Does the bank (Plaid) say this is a transfer or a credit card payment?"""
    return (t["pfc_primary"] in TRANSFER_PFC_PRIMARY) or t["pfc_detailed"] == CARD_PAYMENT_PFC


def _cents(amount):
    return int(round(amount * 100))


def _days(a, b):
    from datetime import date
    return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)


def clean_up(conn):
    """Undo pairs that no longer hold: the other side was deleted or changed, a side was given a category that
    isn't [Transfer], or both ended up in the same account."""
    tid = transfer_category(conn)
    debts = sorted(debt_ids(conn)) or [-1]
    marks = ",".join("?" * len(debts))
    conn.execute(f"""UPDATE transactions SET transfer_pair = NULL
                     WHERE transfer_pair IS NOT NULL AND (category_id IS NULL OR category_id != ?)
                       AND NOT (amount < 0 AND category_id IN ({marks}) AND EXISTS (
                           SELECT 1 FROM transactions o JOIN accounts oa ON oa.id = o.account_id
                           JOIN accounts a ON a.id = transactions.account_id
                           WHERE o.id = transactions.transfer_pair AND oa.kind IN ('credit', 'loan')
                             AND a.kind NOT IN ('credit', 'loan')))""", [tid] + debts)
    conn.execute("""UPDATE transactions SET transfer_pair = NULL WHERE transfer_pair IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM transactions o WHERE o.id = transactions.transfer_pair
                        AND o.transfer_pair = transactions.id AND o.account_id != transactions.account_id
                        AND ROUND(o.amount + transactions.amount, 2) = 0)""")


def match(conn):
    """Pair up transfers between your accounts. Returns how many pairs were made."""
    clean_up(conn)
    tid = transfer_category(conn)
    debts = debt_ids(conn)
    kinds = _kinds(conn)
    rows = [r for r in conn.execute(
        """SELECT id, account_id, date, amount, category_id FROM transactions
           WHERE transfer_pair IS NULL AND is_opening = 0 AND amount != 0
             AND COALESCE(transfer_state, '') != 'no'
             AND (plaid_id IS NULL OR cleared != '')
           ORDER BY date, id""")
            if r["category_id"] in (None, tid) or (r["amount"] < 0 and r["category_id"] in debts)]
    deposits = {}
    for r in rows:
        if r["amount"] > 0:
            deposits.setdefault(_cents(r["amount"]), []).append(r)
    used, pairs = set(), 0
    for out in rows:
        if out["amount"] >= 0:
            continue
        best = None
        for dep in deposits.get(-_cents(out["amount"]), []):
            if dep["id"] in used or dep["account_id"] == out["account_id"]:
                continue
            if out["category_id"] in debts and not is_debt_payment(kinds, out["account_id"], dep["account_id"]):
                continue  # you filed it as Debt: only a payment into a card or loan account can match
            gap = _days(out["date"], dep["date"])
            if gap <= MATCH_DAYS and (best is None or gap < best[0]):
                best = (gap, dep)
        if best:
            dep = best[1]
            used.add(dep["id"])
            out_cat = tid
            if is_debt_payment(kinds, out["account_id"], dep["account_id"]):  # counts as debt paid
                out_cat = out["category_id"] if out["category_id"] in debts else debt_category(conn, int(out["date"][:4]))
            conn.execute("UPDATE transactions SET transfer_pair = ?, transfer_state = NULL, category_id = ? WHERE id = ?",
                         (dep["id"], out_cat, out["id"]))
            conn.execute("UPDATE transactions SET transfer_pair = ?, transfer_state = NULL, category_id = ? WHERE id = ?",
                         (out["id"], tid, dep["id"]))
            pairs += 1
    return pairs


def debt_upgrade(conn):
    """Once: card and loan payments matched with [Transfer] on both sides (before they counted as debt paid) get
    Debt on the paying side."""
    if db.get_setting(conn, "debt_payments_done"):
        return
    tid, kinds = transfer_category(conn), _kinds(conn)
    for r in conn.execute("""SELECT t.id, t.account_id, t.date, o.account_id AS dep_account FROM transactions t
                             JOIN transactions o ON o.id = t.transfer_pair
                             WHERE t.amount < 0 AND t.category_id = ?""", (tid,)).fetchall():
        if is_debt_payment(kinds, r["account_id"], r["dep_account"]):
            conn.execute("UPDATE transactions SET category_id = ? WHERE id = ?",
                         (debt_category(conn, int(r["date"][:4])), r["id"]))
    db.set_setting(conn, "debt_payments_done", "1")


def category_changed(conn, tx, category_id):
    """You picked a category for a transaction. Picking something other than [Transfer] for one side of a pair
    means it isn't a transfer (it won't be matched again); picking [Transfer] makes it eligible again."""
    tid = transfer_category(conn)
    if category_id in debt_ids(conn) and tx["amount"] < 0:
        if tx["transfer_pair"] is not None:
            other = conn.execute("SELECT account_id FROM transactions WHERE id = ?", (tx["transfer_pair"],)).fetchone()
            if other and is_debt_payment(_kinds(conn), tx["account_id"], other["account_id"]):
                return  # a card or loan payment: Debt on this side, [Transfer] on the card's side
            conn.execute("UPDATE transactions SET transfer_state = 'no', transfer_pair = NULL WHERE id = ?", (tx["id"],))
        else:  # can still be matched with the card's side
            conn.execute("UPDATE transactions SET transfer_state = NULL WHERE id = ? AND transfer_state = 'no'",
                         (tx["id"],))
        return
    if category_id == tid:
        conn.execute("UPDATE transactions SET transfer_state = CASE WHEN transfer_state = 'no' THEN NULL "
                     "ELSE transfer_state END WHERE id = ?", (tx["id"],))
    elif tx["transfer_pair"] is not None or (tx["category_id"] == tid and category_id is not None):
        conn.execute("UPDATE transactions SET transfer_state = 'no', transfer_pair = NULL WHERE id = ?", (tx["id"],))
    elif category_id is not None and looks_like_transfer(tx):
        conn.execute("UPDATE transactions SET transfer_state = 'no' WHERE id = ?", (tx["id"],))


def not_a_transfer(conn, tx_id):
    """Undo a pair (both sides go back to Uncategorized) and never match them again."""
    tid = transfer_category(conn)
    tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx:
        return
    ids = [tx_id] + ([tx["transfer_pair"]] if tx["transfer_pair"] else [])
    reset = [tid] + (sorted(debt_ids(conn)) if tx["transfer_pair"] else [])  # a matched card payment's Debt too
    marks = ",".join("?" * len(reset))
    for i in ids:
        conn.execute(f"""UPDATE transactions SET transfer_pair = NULL, transfer_state = 'no',
                         category_id = CASE WHEN category_id IN ({marks}) THEN NULL ELSE category_id END
                         WHERE id = ?""", reset + [i])


def outside(conn, tx_id):
    """A transfer to or from an account you don't track here: [Transfer], and nothing to check."""
    tid = transfer_category(conn)
    conn.execute("UPDATE transactions SET transfer_state = 'outside', category_id = ?, category_locked = 1 WHERE id = ?",
                 (tid, tx_id))


def to_check(conn):
    """{transaction id: why} for transfers that need a look."""
    tid = transfer_category(conn)
    out = {}
    for r in conn.execute(
            """SELECT t.*, c.kind AS ck FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
               WHERE t.is_opening = 0 AND t.transfer_pair IS NULL AND t.transfer_state IS NULL
                 AND (t.plaid_id IS NULL OR t.cleared != '')"""):
        if r["category_id"] == tid:
            out[r["id"]] = ("[Transfer] with no matching other side in your accounts. If it went to or came from an "
                            "account you don't track here, that's fine: click Other side isn't in Budget Tool.")
        elif r["category_id"] is None and card_payment_out(r):
            out[r["id"]] = "Your bank says this is a credit card payment. Give it Debt to count it as debt paid."
        elif r["category_id"] is None and looks_like_transfer(r):
            out[r["id"]] = "Your bank says this is a transfer or card payment. If so, give it [Transfer]."
    return out
