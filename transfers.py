"""Money moved between your own accounts: it's neither income nor spending, so it must stay out of the numbers.

- Matching: a payment in one account and a deposit of the same amount in another, within MATCH_DAYS of each other,
  are paired (transactions.transfer_pair points at the other side) and both get the [Transfer] category, which
  reports leave out. Only transactions that are Uncategorized or already [Transfer] are matched; a category you
  picked is never changed. Pending bank charges wait until they post. "Not a transfer" undoes a pair for good.
- Suggestions: Plaid marks transfers and credit card payments. When the other side isn't in Budget Tool, those
  get a one-click "[Transfer]?" suggestion instead of being categorized for you.
- Checking: [Transfer]s with no other side, transfer-looking transactions that are still Uncategorized, and card
  payments filed as spending are listed for you to look at (To do on the Overview, "Transfers to check" on the
  Register). "Other side isn't in Budget Tool" marks a one-sided transfer as fine.

transactions.transfer_state: NULL (nothing decided), 'no' (you said it isn't a transfer), 'outside' (a transfer
whose other side is an account you don't track here).
"""
import db

MATCH_DAYS = 5
TRANSFER_PFC_PRIMARY = ("TRANSFER_IN", "TRANSFER_OUT")
CARD_PAYMENT_PFC = "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"


def transfer_category(conn):
    return db.ensure_category(conn, db.TRANSFER, "transfer")


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
    conn.execute("""UPDATE transactions SET transfer_pair = NULL
                    WHERE transfer_pair IS NOT NULL AND (category_id IS NULL OR category_id != ?)""", (tid,))
    conn.execute("""UPDATE transactions SET transfer_pair = NULL WHERE transfer_pair IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM transactions o WHERE o.id = transactions.transfer_pair
                        AND o.transfer_pair = transactions.id AND o.account_id != transactions.account_id
                        AND ROUND(o.amount + transactions.amount, 2) = 0)""")


def match(conn):
    """Pair up transfers between your accounts. Returns how many pairs were made."""
    clean_up(conn)
    tid = transfer_category(conn)
    rows = conn.execute(
        """SELECT id, account_id, date, amount FROM transactions
           WHERE transfer_pair IS NULL AND is_opening = 0 AND amount != 0
             AND COALESCE(transfer_state, '') != 'no'
             AND (plaid_id IS NULL OR cleared != '')
             AND (category_id IS NULL OR category_id = ?)
           ORDER BY date, id""", (tid,)).fetchall()
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
            gap = _days(out["date"], dep["date"])
            if gap <= MATCH_DAYS and (best is None or gap < best[0]):
                best = (gap, dep)
        if best:
            dep = best[1]
            used.add(dep["id"])
            conn.execute("UPDATE transactions SET transfer_pair = ?, transfer_state = NULL, category_id = ? WHERE id = ?",
                         (dep["id"], tid, out["id"]))
            conn.execute("UPDATE transactions SET transfer_pair = ?, transfer_state = NULL, category_id = ? WHERE id = ?",
                         (out["id"], tid, dep["id"]))
            pairs += 1
    return pairs


def category_changed(conn, tx, category_id):
    """You picked a category for a transaction. Picking something other than [Transfer] for one side of a pair
    means it isn't a transfer (it won't be matched again); picking [Transfer] makes it eligible again."""
    tid = transfer_category(conn)
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
    for i in ids:
        conn.execute("""UPDATE transactions SET transfer_pair = NULL, transfer_state = 'no',
                        category_id = CASE WHEN category_id = ? THEN NULL ELSE category_id END WHERE id = ?""", (tid, i))


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
        elif r["category_id"] is None and looks_like_transfer(r):
            out[r["id"]] = "Your bank says this is a transfer or card payment. If so, give it [Transfer]."
        elif r["ck"] == "expense" and r["pfc_detailed"] == CARD_PAYMENT_PFC:
            out[r["id"]] = ("A credit card payment filed as spending. The card's purchases already count as spending, "
                            "so this counts it twice. Give it [Transfer] unless it really is spending.")
    return out
