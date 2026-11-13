"""Synthetic data only: Debt? / [Transfer]? suggestion chips; nothing categorized for you."""
import os, shutil, sys
import helpers
proj, work, src = helpers.paths_for_test()
shutil.rmtree(work, ignore_errors=True); os.makedirs(work)
shutil.copy(src, os.path.join(work, "budget.db"))
os.environ["BUDGETTOOL_DATA"] = work
sys.path.insert(0, proj)
FAILS = []
def ok(c, label):
    print(("PASS " if c else "FAIL ") + label)
    if not c: FAILS.append(label)

import unlock_for_tests  # noqa
import app as A, db
conn = db.connect()
chk = conn.execute("INSERT INTO accounts (name, kind, sort, auto_opening) VALUES ('T Checking','checking',50,0)").lastrowid
def tx(d, amount, payee, pfc=None, pfcd=None):
    return conn.execute("""INSERT INTO transactions (account_id, date, payee, amount, plaid_id, cleared, pfc_primary, pfc_detailed)
                           VALUES (?,?,?,?,?, 'c', ?, ?)""", (chk, d, payee, amount, "x" + payee, pfc, pfcd)).lastrowid
card = tx("2026-03-01", -120.12, "ZZ CARD PMT", "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
zelle = tx("2026-03-02", -40.04, "ZZ ZELLE", "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER")
plain = tx("2026-03-03", -9.99, "ZZ NEW SHOP")
conn.commit()
row = lambda i: conn.execute("SELECT * FROM transactions WHERE id=?", (i,)).fetchone()
s = A.category_suggestions(conn, [row(card), row(zelle), row(plain)])
ok(s.get(card, (0, ""))[1] == "Debt" and "debt paid" in s[card][2], "card payment going out -> Debt?")
ok(s.get(zelle, (0, ""))[1] == db.TRANSFER, "bank-marked transfer -> [Transfer]?")
ok(plain not in s, "ordinary new payee: no chip")
ok(all(row(i)["category_id"] is None for i in (card, zelle, plain)), "nothing categorized for you")
cl = A.app.test_client(); cl.get("/?key=" + A.LAUNCH_KEY)
h = cl.get(f"/register?month=all&account={chk}").data.decode()
ok("Debt?" in h and "[Transfer]?" in h and "debt paid" in h, "Register shows the chips with their tooltips")
for p in ("/", "/register", "/reports", "/accounts", "/budget", "/export"):
    ok(cl.get(p).status_code == 200, f"{p} loads")
ok(not os.path.exists(os.path.join(proj, "transfers.py")), "transfers.py is gone")
print("\n" + ("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
