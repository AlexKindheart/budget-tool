"""Synthetic data only: over-budget alerts, bulk categorize, splits, bills + safe to spend, carry-over, balancing a
statement, spending by payee, net worth label, old unencrypted copy."""
import html, os, shutil, sqlite3, sys
from datetime import date, timedelta
import helpers
proj, work, src = helpers.paths_for_test()
shutil.rmtree(work, ignore_errors=True); os.makedirs(work)
shutil.copy(src, os.path.join(work, "budget.db"))
os.environ["BUDGETTOOL_DATA"] = work
sys.path.insert(0, proj)
import paths
oldcopy = os.path.join(work, "old-installed")  # a pretend %APPDATA%\BudgetTool, never the real one
os.makedirs(oldcopy)
shutil.copy(src, os.path.join(oldcopy, "budget.db"))
open(os.path.join(oldcopy, "notes.txt"), "w").write("not ours")
paths.old_data_dirs = lambda: [oldcopy]
FAILS = []
def ok(c, label):
    print(("PASS " if c else "FAIL ") + label)
    if not c: FAILS.append(label)
import unlock_for_tests  # noqa
import app as A, db, reports, bills, duplicates
cl = A.app.test_client(); cl.get("/?key=" + A.LAUNCH_KEY); cl.get("/")
with cl.session_transaction() as s: tok = s["csrf"]
page = lambda r: html.unescape(r.data.decode())
conn = db.connect()
today = date.today()
m0 = date(today.year, today.month, 1)
acc = conn.execute("INSERT INTO accounts (name, kind, sort, auto_opening) VALUES ('F Checking','checking',70,0)").lastrowid
card = conn.execute("INSERT INTO accounts (name, kind, sort, auto_opening) VALUES ('F Visa','credit',71,0)").lastrowid
def newcat(name, kind="expense"):
    return conn.execute("INSERT INTO categories (name, kind, sort) VALUES (?, ?, 900)", (name, kind)).lastrowid
dining, groc, home, car, pay = newcat("F Dining"), newcat("F Groceries"), newcat("F Home"), newcat("F Car"), newcat("F Pay", "income")
def tx(d, amount, payee, cat=None, a=None):
    return conn.execute("INSERT INTO transactions (account_id, date, payee, amount, category_id) VALUES (?,?,?,?,?)",
                        (a or acc, d.isoformat(), payee, amount, cat)).lastrowid
def budget(cid, month, amount):
    conn.execute("INSERT OR REPLACE INTO budgets (category_id, month, amount) VALUES (?,?,?)", (cid, month, amount))

# ---- 4: over budget / on pace
budget(dining, reports.month_key(m0), 100); budget(groc, reports.month_key(m0), 400)
tx(m0, -130, "Restaurant", dining)
day = max(today.day, 1)
tx(m0, -min(399, 400 * 0.6 * 31 / max(day, 7) if day >= 7 else 0) if day >= 7 else -1, "Supermarket", groc)
conn.commit()
al = {a["name"]: a for a in reports.budget_alerts(conn, today)}
ok("F Dining" in al and al["F Dining"]["over"], "4: over-budget category is flagged")
h = page(cl.get("/"))
ok("F Dining is over budget" in h, "4: To do says Dining is over budget")
pace = reports.budget_alerts(conn, date(today.year, today.month, 10))
ok(all(a["name"] != "F Groceries" or not a["over"] for a in pace), "4: on-pace warning isn't called over budget")

# ---- 5: bulk categorize
u1, u2, d1 = tx(today, -5.5, "Snack A"), tx(today, -6.5, "Snack B"), tx(today, 50, "Refund-ish")
conn.commit()
r = cl.post("/register/bulk-category", data={"csrf": tok, "ids": [str(u1), str(u2), str(d1)], "category_id": str(dining), "next": "/register"}, follow_redirects=True)
row = lambda i: conn.execute("SELECT * FROM transactions WHERE id=?", (i,)).fetchone()
ok(row(u1)["category_id"] == dining and row(u2)["category_id"] == dining, "5: ticked payments get the category")
ok(row(d1)["category_id"] is None and "didn't fit" in page(r), "5: a deposit can't take an expense category, and you're told")
h = page(cl.get("/register?month=all"))
ok("data-bulk" in h and 'name="ids"' in h, "5: Register has tick boxes and the bulk bar")

# ---- 6: splits
w = tx(m0, -150, "Walmart")
conn.commit()
r = cl.post(f"/register/{w}/splits", data={"csrf": tok, "split_category": [str(groc), str(home), ""], "split_amount": ["100", "40", ""], "split_memo": ["", "", ""]}, follow_redirects=True)
ok("add up to $140.00" in page(r) and not conn.execute("SELECT 1 FROM splits WHERE tx_id=?", (w,)).fetchone(), "6: parts must add up")
r = cl.post(f"/register/{w}/splits", data={"csrf": tok, "split_category": [str(groc), str(pay)], "split_amount": ["100", "50"], "split_memo": ["", ""]}, follow_redirects=True)
ok("income category" in page(r), "6: a payment's parts need expense categories")
before = reports.actuals(conn, m0, today)
cl.post(f"/register/{w}/splits", data={"csrf": tok, "split_category": [str(groc), str(home)], "split_amount": ["100", "50"], "split_memo": ["food", ""]})
after = reports.actuals(conn, m0, today)
ok(round(after.get(groc, 0) - before.get(groc, 0), 2) == 100 and round(after.get(home, 0) - before.get(home, 0), 2) == 50,
   "6: Reports count $100 groceries + $50 home")
ok(row(w)["category_id"] is None, "6: the transaction itself has no category")
unc_ids = [r["id"] for r in conn.execute("SELECT t.id FROM transactions t WHERE t.category_id IS NULL AND NOT EXISTS (SELECT 1 FROM splits s WHERE s.tx_id=t.id)")]
ok(w not in unc_ids, "6: a split one doesn't count as uncategorized")
h = page(cl.get(f"/register?month=all&category={home}"))
ok("Walmart" in h and "split" in h, "6: filtering by Home finds the split transaction")
h = page(cl.get("/register?month=all&category=none")); ok("Walmart" not in h, "6: ...and Uncategorized doesn't")
r = cl.post(f"/register/{w}/category", data={"csrf": tok, "category_id": str(groc)}, headers={"X-Requested-With": "fetch"})
ok(r.status_code == 400 and "split" in r.json["error"], "6: can't overwrite a split with one category in place")
import export
ex = export._sums(conn, m0, today, [acc])
ok(round(-ex.get(home, 0), 2) >= 50, "6: Export sums use the parts")
cl.post(f"/register/{w}", data={"csrf": tok, "account_id": str(acc), "date": m0.isoformat(), "payee": "Walmart", "memo": "", "payment": "160", "deposit": "", "next": "/register"})
ok(not conn.execute("SELECT 1 FROM splits WHERE tx_id=?", (w,)).fetchone(), "6: changing the amount removes the split (with a message)")
cl.post(f"/register/{w}", data={"csrf": tok, "account_id": str(acc), "date": m0.isoformat(), "payee": "Walmart", "memo": "", "payment": "150", "deposit": "", "next": "/register"})
cl.post(f"/register/{w}/splits", data={"csrf": tok, "split_category": [str(groc), str(home)], "split_amount": ["100", "50"], "split_memo": ["", ""]})
cl.post(f"/register/{w}/splits", data={"csrf": tok, "action": "remove"})
ok(not conn.execute("SELECT 1 FROM splits WHERE tx_id=?", (w,)).fetchone(), "6: Stop splitting it")
# duplicates keep a split
linked = conn.execute("INSERT INTO accounts (name, kind, plaid_account_id, sort, auto_opening) VALUES ('F Linked','checking','pa-f',72,0)").lastrowid
mine = conn.execute("INSERT INTO transactions (account_id, date, payee, amount) VALUES (?,?,?,?)", (linked, m0.isoformat(), "Costco", -80)).lastrowid
conn.executemany("INSERT INTO splits (tx_id, category_id, amount) VALUES (?,?,?)", [(mine, groc, -60), (mine, home, -20)])
bank = conn.execute("INSERT INTO transactions (plaid_id, account_id, date, payee, amount, cleared) VALUES ('pf1',?,?,?,?, 'c')", (linked, (m0 + timedelta(days=2)).isoformat(), "COSTCO WHSE", -80)).lastrowid
duplicates.match(conn); conn.commit()
ok(conn.execute("SELECT COUNT(*) FROM splits WHERE tx_id=?", (bank,)).fetchone()[0] == 2, "6: combining with the bank's copy keeps your split")
duplicates.split(conn, bank); conn.commit()
back = conn.execute("SELECT id FROM transactions WHERE account_id=? AND plaid_id IS NULL AND amount=-80", (linked,)).fetchone()[0]
ok(conn.execute("SELECT COUNT(*) FROM splits WHERE tx_id=?", (back,)).fetchone()[0] == 2
   and not conn.execute("SELECT 1 FROM splits WHERE tx_id=?", (bank,)).fetchone(), "6: Not the same gives the split back to your entry")

# ---- 7: bills, safe to spend
conn.execute("UPDATE accounts SET closed = 1 WHERE id != ? AND kind IN ('checking', 'cash')", (acc,))
conn.commit()
bal = A.account_balances(conn)[acc][0]
r = cl.post("/bills", data={"csrf": tok, "action": "add", "name": "Rent", "amount": "1000", "kind": "bill", "every": "month",
                           "next_date": (today + timedelta(days=5)).isoformat(), "account_id": str(acc), "category_id": ""}, follow_redirects=True)
cl.post("/bills", data={"csrf": tok, "action": "add", "name": "Phone", "amount": "60", "kind": "bill", "every": "month",
                        "next_date": (today + timedelta(days=20)).isoformat(), "account_id": str(acc), "category_id": ""})
cl.post("/bills", data={"csrf": tok, "action": "add", "name": "Payday", "amount": "2000", "kind": "income", "every": "2weeks",
                        "next_date": (today + timedelta(days=10)).isoformat(), "account_id": str(acc), "category_id": ""})
safe = bills.safe_to_spend(conn, A.account_balances(conn))
ok(safe["bills"] == 1000 and safe["until"] == today + timedelta(days=10), "7: only bills before the next paycheck count (rent, not phone)")
ok(abs(safe["amount"] - (bal - 1000)) < 0.01, f"7: safe to spend = checking - rent ({safe['amount']})")
h = page(cl.get("/"))
ok("Safe to spend" in h and "Rent" in h and "Payday" in h, "7: Overview shows Coming up and Safe to spend")
r = cl.post("/bills", data={"csrf": tok, "action": "add", "name": "", "amount": "5", "every": "month", "next_date": today.isoformat()}, follow_redirects=True)
ok("Give it a name" in page(r), "7: a bill needs a name")
rent = conn.execute("SELECT * FROM bills WHERE name='Rent'").fetchone()
tx(today + timedelta(days=3), -1000, "Landlord"); conn.commit()   # paid early
bills.refresh(conn, today); conn.commit()
ok(conn.execute("SELECT next_date FROM bills WHERE id=?", (rent["id"],)).fetchone()[0] == bills.step(today + timedelta(days=5), "month").isoformat(),
   "7: paying it moves the bill to next month")
conn.execute("UPDATE bills SET next_date=? WHERE name='Phone'", ((today - timedelta(days=10)).isoformat(),)); conn.commit()
bills.refresh(conn, today); conn.commit()
ok(date.fromisoformat(conn.execute("SELECT next_date FROM bills WHERE name='Phone'").fetchone()[0]) > today, "7: a past-due bill moves on by itself")
ok(bills.step(date(2026, 1, 31), "month") == date(2026, 2, 28) and bills.step(date(2026, 1, 31), "month", 2) == date(2026, 3, 31), "7: month ends handled")
h = page(cl.get(f"/bills?from_tx={u1}")); ok('value="Snack A"' in h, "7: Make this a repeating bill pre-fills the form")

# ---- 8: carry over
y = today.year - 1
for mth in (1, 2, 3):
    budget(car, f"{y}-{mth:02d}", 100)
tx(date(y, 1, 15), -30, "Oil change", car)
conn.commit()
cl.post("/categories", data={"csrf": tok, "action": "save", **{f"name_{c['id']}": c["name"] for c in conn.execute("SELECT id, name FROM categories")},
                             **{f"kind_{c['id']}": c["kind"] for c in conn.execute("SELECT id, kind FROM categories")}, f"rollover_{car}": "1"})
ok(conn.execute("SELECT rollover FROM categories WHERE id=?", (car,)).fetchone()[0] == 1, "8: Categories saves Carry over")
mar = reports.budget_vs_actual(conn, date(y, 3, 1), date(y, 3, 31))
r8 = next(r for r in mar["expense"] if r["id"] == car)
ok(r8["carried"] == 170 and r8["budget"] == 270, f"8: March has $100 + $170 carried ({r8['budget']})")
jan = next(r for r in reports.budget_vs_actual(conn, date(y + 1, 1, 1), date(y + 1, 1, 31))["expense"] if r["id"] == car) if any(r["id"] == car for r in reports.budget_vs_actual(conn, date(y + 1, 1, 1), date(y + 1, 1, 31))["expense"]) else None
ok(jan is None or not jan["carried"], "8: a new year starts fresh")
h = page(cl.get(f"/reports?view=monthly&month={y}-03")); ok("carried" in h, "8: Reports show what was carried")
ok(page(cl.get("/categories")).count("carry over") >= 2, "8: Categories has the Carry over boxes")

# ---- 9: balance against a statement
cash = conn.execute("INSERT INTO accounts (name, kind, sort, auto_opening) VALUES ('F Wallet','cash',73,0)").lastrowid
c1 = tx(date(today.year, 1, 2), 200, "ATM", None, cash); c2 = tx(date(today.year, 1, 5), -20, "Lunch", None, cash); c3 = tx(today, -5, "Later", None, cash)
conn.commit()
h = page(cl.get(f"/accounts/{cash}/reconcile?date={today.year}-01-31"))
ok("ATM" in h and "Lunch" in h and "Later" not in h or today <= date(today.year, 1, 31), "9: lists transactions up to the statement date")
r = cl.post(f"/accounts/{cash}/reconcile", data={"csrf": tok, "date": f"{today.year}-01-31", "balance": "190", "ids": [str(c1), str(c2)]}, follow_redirects=True)
ok("off by" in page(r) and row(c1)["cleared"] == "", "9: doesn't finish when it doesn't balance")
r = cl.post(f"/accounts/{cash}/reconcile", data={"csrf": tok, "date": f"{today.year}-01-31", "balance": "180", "ids": [str(c1), str(c2)]}, follow_redirects=True)
ok(row(c1)["cleared"] == "R" and row(c2)["cleared"] == "R" and "matches your statement" in page(r), "9: balanced: marked reconciled")
h = page(cl.get(f"/accounts/{cash}/reconcile?date={today.isoformat()}")); ok("ATM" not in h and "Already reconciled $180.00" in h, "9: reconciled ones aren't listed again")
ok("reconciled" in page(cl.get(f"/register?month=all&account={cash}")), "9: Register labels them reconciled")
ok("Balance it" in page(cl.get("/accounts")), "9: Accounts has Balance it")
r = cl.get(f"/accounts/{linked}/reconcile", follow_redirects=True); ok("stay in step" in page(r), "9: bank-linked accounts don't need it")

# ---- 10: payees, net worth label
for i in range(4):
    tx(date(y, 4 + i, 3), -15.99, "StreamFlix", dining)
conn.commit()
rows = {r["payee"]: r for r in reports.payees(conn, date(y, 1, 1), date(y, 12, 31))}
ok("StreamFlix" in rows and rows["StreamFlix"]["repeats"] and abs(rows["StreamFlix"]["spent"] - 63.96) < .01, "10: payee totals and 'repeats' for a subscription")
ok("Oil change" in rows and not rows["Oil change"]["repeats"], "10: a one-off isn't marked repeats")
h = page(cl.get(f"/reports?view=payees&year={y}")); ok("StreamFlix" in h and "repeats" in h and "By payee" in h, "10: By payee report")
ok("All accounts (net worth)" in page(cl.get("/")), "10: the balance chart's combined line is labeled net worth")

# ---- 12: the old unencrypted copy
bigger = sqlite3.connect(os.path.join(oldcopy, "budget.db"))
bigger.execute("INSERT INTO transactions (account_id, date, payee, amount) VALUES (1, '2099-01-01', 'Only in the old copy', -1)")
bigger.commit(); bigger.close()
ok("old, unencrypted copy" not in page(cl.get("/")), "12: an old copy with history your budget doesn't have is never offered for deletion")
bigger = sqlite3.connect(os.path.join(oldcopy, "budget.db"))
bigger.execute("DELETE FROM transactions WHERE payee = 'Only in the old copy'"); bigger.commit(); bigger.close()
h = page(cl.get("/")); ok("old, unencrypted copy" in h and oldcopy in h, "12: To do points out the old copy")
h = page(cl.get("/old-copy")); ok("budget.db" in h and "notes.txt" not in h, "12: lists only Budget Tool's own files")
r = cl.post("/old-copy", data={"csrf": tok, "folder": "C:\\Windows", "action": "delete"})
ok(os.path.exists(os.path.join(oldcopy, "budget.db")), "12: won't delete anything from a folder it didn't list")
cl.post("/old-copy", data={"csrf": tok, "folder": oldcopy, "action": "delete"})
ok(not os.path.exists(os.path.join(oldcopy, "budget.db")) and os.path.exists(os.path.join(oldcopy, "notes.txt")), "12: Delete removes the old copy only")
ok("old, unencrypted copy" not in page(cl.get("/")), "12: ...and the To do item goes away")

for p in ("/", "/register?month=all", "/reports", "/reports?view=yearly", "/reports?view=payees", "/bills", "/accounts",
          "/categories", "/budget", "/export", "/settings"):
    ok(cl.get(p).status_code == 200, f"{p} loads")
print("\n" + ("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
