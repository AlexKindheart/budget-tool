"""Synthetic data only: password lock, encryption at rest, backups, restore, recovery code, combined duplicates."""
import html, json, os, re, shutil, sqlite3, sys, time
from datetime import date, timedelta
import helpers
proj, work, src = helpers.paths_for_test()
shutil.rmtree(work, ignore_errors=True); os.makedirs(work)
shutil.copy(src, os.path.join(work, "budget.db"))
shutil.copy(src, os.path.join(work, "budget.db.v2-backup-20250101-000000"))  # an old unencrypted upgrade copy
os.environ["BUDGETTOOL_DATA"] = work
sys.path.insert(0, proj)
FAILS = []
def ok(c, label):
    print(("PASS " if c else "FAIL ") + label)
    if not c: FAILS.append(label)

plain = sqlite3.connect(os.path.join(work, "budget.db"))
n_tx = plain.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
secret_payee = plain.execute("SELECT payee FROM transactions WHERE length(payee) > 8 AND payee NOT LIKE '[%' AND payee NOT LIKE 'Opening%' ORDER BY id DESC LIMIT 1").fetchone()[0]
plain.close()
raw = lambda p: open(p, "rb").read()
ok(secret_payee.encode() in raw(os.path.join(work, "budget.db")), f"fake data starts unencrypted ({secret_payee!r} readable)")

import app as A, db, security, duplicates, updater
ok(not db.unlocked(), "starts locked")
try:
    db.connect(); ok(False, "connect refused while locked")
except db.Locked:
    ok(True, "connect refused while locked")
cl = A.app.test_client()
r = cl.get("/?key=" + A.LAUNCH_KEY)
ok(r.status_code == 302 and "/setup" in r.headers["Location"], "first open asks for a password")
cl.get("/setup")
with cl.session_transaction() as s: tok = s["csrf"]
ok(cl.get("/register").status_code == 302, "Register is closed while locked")
ok(cl.get("/api/sync-status").status_code == 401, "API is closed while locked")
ok(cl.get("/export").status_code == 302 and cl.get("/settings").status_code == 302, "every screen redirects")
h = html.unescape(cl.get("/setup").data.decode())
ok("Protect your budget" in h and "Register" not in h, "setup screen, no menu")
ok(A.STARTUP_SYNC["running"] is False and not A.LOCK["started"], "no bank sync before unlocking")

r = cl.post("/setup", data={"csrf": tok, "password": "short", "confirm": "short"}, follow_redirects=True)
ok("at least 10" in html.unescape(r.data.decode()) and not security.is_set_up(), "short password refused")
r = cl.post("/setup", data={"csrf": tok, "password": "maple tractor sunset", "confirm": "maple tractor sunsex"}, follow_redirects=True)
ok("don't match" in html.unescape(r.data.decode()), "mismatched passwords refused")
PW = "maple tractor sunset"
t0 = time.time()
r = cl.post("/setup", data={"csrf": tok, "password": PW, "confirm": PW})
h = html.unescape(r.data.decode())
m = re.search(r'recovery-code">([A-Z2-7-]+)<', h)
ok(r.status_code == 200 and m, f"password set, recovery code shown once ({time.time() - t0:.1f}s)")
CODE = m.group(1) if m else ""
ok(db.unlocked(), "unlocked after setting the password")

dbfile = os.path.join(work, "budget.db")
ok(not db.is_plaintext(dbfile) and secret_payee.encode() not in raw(dbfile), "budget.db is encrypted (payee no longer readable)")
with db.connect() as conn:
    ok(conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == n_tx, f"all {n_tx} transactions still there")
    ok(conn.execute("PRAGMA cipher_version").fetchone()[0].startswith("4."), "SQLCipher 4")
ok(not os.path.exists(os.path.join(work, "budget.db.v2-backup-20250101-000000")), "old unencrypted upgrade copy removed")
bk = security.backups()
ok(any(b["name"].endswith("-older-version.db") for b in bk), "...and kept as an encrypted backup")
ok(any(b["auto"] for b in bk), "today's automatic backup made")
for b in bk:
    ok(secret_payee.encode() not in raw(os.path.join(security.BACKUP_DIR, b["name"])), f"backup {b['name']} is encrypted")
lock_txt = open(security.LOCK_PATH, encoding="utf-8").read()
ok(PW not in lock_txt and CODE not in lock_txt and "password" in json.loads(lock_txt), "lock.json holds no password or code")
ok(os.path.exists(os.path.join(security.BACKUP_DIR, "lock.json")), "backups folder has its own copy of lock.json")
leftovers = [f for f in os.listdir(work) if os.path.isfile(os.path.join(work, f)) and db.is_plaintext(os.path.join(work, f))]
ok(not leftovers, f"no unencrypted database files left in the data folder {leftovers}")

# wrong keys
try:
    db.verify(dbfile, key=os.urandom(32)); ok(False, "a random key can't open the budget")
except ValueError:
    ok(True, "a random key can't open the budget")
try:
    plain = sqlite3.connect(dbfile); plain.execute("SELECT * FROM sqlite_master").fetchall(); ok(False, "plain SQLite can't read it")
except sqlite3.DatabaseError:
    ok(True, "plain SQLite can't read it")

h = html.unescape(cl.get("/").data.decode())
ok("Lock" in h and "data-lock-after=\"15\"" in h, "pages have the Lock button and the 15-minute auto-lock")
ok(cl.get("/settings").status_code == 200 and "Back up now" in html.unescape(cl.get("/settings").data.decode()), "Settings shows Backups")

# lock / unlock
r = cl.get("/lock"); ok(not db.unlocked() and "/unlock" in r.headers["Location"], "Lock locks")
r = cl.get("/register?month=all")
ok(r.status_code == 302 and "next=" in r.headers["Location"], "locked: Register redirects to unlock")
r = cl.post("/unlock", data={"csrf": tok, "password": "wrong password!"}, follow_redirects=True)
ok("isn't right" in html.unescape(r.data.decode()) and not db.unlocked(), "wrong password refused")
r = cl.post("/unlock", data={"csrf": tok, "password": PW, "next": "/register?month=all"})
ok(db.unlocked() and r.headers["Location"].endswith("/register?month=all"), "right password unlocks and goes back to the page")
cl.get("/lock")
r = cl.post("/unlock", data={"csrf": tok, "password": PW, "next": "//evil.example/"})
ok(r.headers["Location"] in ("/", "http://localhost/"), f"next can't point outside Budget Tool ({r.headers['Location']})")

# idle lock
A.LOCK["last_active"] = time.time() - 16 * 60
r = cl.get("/budget")
ok(not db.unlocked() and r.status_code == 302, "locks itself after 15 minutes without use")
cl.post("/unlock", data={"csrf": tok, "password": PW})
A.LOCK["last_active"] = time.time() - 14 * 60
cl.get("/api/still-here")
A.LOCK["last_active"] -= 0  # still-here just refreshed it
ok(db.unlocked() and time.time() - A.LOCK["last_active"] < 5, "using the window keeps it unlocked")
cl.post("/settings/lock-after", data={"csrf": tok, "minutes": "0"})
A.LOCK["last_active"] = time.time() - 10 * 3600
ok(cl.get("/budget").status_code == 200 and db.unlocked(), "'only when closed' never auto-locks")
cl.post("/settings/lock-after", data={"csrf": tok, "minutes": "15"})

# recovery code
cl.get("/lock")
r = cl.post("/recover", data={"csrf": tok, "code": "AAAA-BBBB-CCCC-DDDD-EEEE-FFFF-GGGG-HHHH", "password": "new password one", "confirm": "new password one"}, follow_redirects=True)
ok("isn't right" in html.unescape(r.data.decode()) and not db.unlocked(), "wrong recovery code refused")
r = cl.post("/recover", data={"csrf": tok, "code": CODE.lower().replace("-", " "), "password": "new password one", "confirm": "new password one"})
ok(db.unlocked(), "recovery code sets a new password (spaces / lowercase are fine)")
cl.get("/lock")
cl.post("/unlock", data={"csrf": tok, "password": PW}); ok(not db.unlocked(), "old password no longer works")
cl.post("/unlock", data={"csrf": tok, "password": "new password one"}); ok(db.unlocked(), "new password works")

# change password / new recovery code from Settings
r = cl.post("/settings/password", data={"csrf": tok, "current": "nope nope nope", "password": "second password!", "confirm": "second password!"}, follow_redirects=True)
ok("isn't right" in html.unescape(r.data.decode()), "change password needs the current one")
cl.post("/settings/password", data={"csrf": tok, "current": "new password one", "password": "second password!", "confirm": "second password!"})
r = cl.post("/settings/recovery-code", data={"csrf": tok, "current": "second password!"})
m2 = re.search(r'recovery-code">([A-Z2-7-]+)<', html.unescape(r.data.decode())); CODE2 = m2.group(1) if m2 else ""
ok(CODE2 and CODE2 != CODE, "new recovery code made")
try:
    security.unlock_with_code(CODE); ok(False, "old recovery code stops working")
except security.WrongPassword:
    ok(True, "old recovery code stops working")
ok(len(security.unlock_with_code(CODE2)) == 32, "new recovery code works")
# old backups still open with the key (password changes re-wrap the same key)
first_auto = [b for b in security.backups() if b["auto"]][0]
db.verify(os.path.join(security.BACKUP_DIR, first_auto["name"])); ok(True, "older backups open after a password change")

# backup now + restore
r = cl.post("/backups/now", data={"csrf": tok}, follow_redirects=True)
ok("Backed up" in html.unescape(r.data.decode()), "Back up now")
manual = next(b["name"] for b in security.backups() if b["name"].endswith("-manual.db"))
with db.connect() as conn:
    conn.execute("DELETE FROM transactions WHERE id IN (SELECT id FROM transactions WHERE is_opening = 0 LIMIT 5)")
    after_delete = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
r = cl.post("/backups/restore", data={"csrf": tok, "name": manual, "current": "wrong wrong wrong"}, follow_redirects=True)
ok("isn't right" in html.unescape(r.data.decode()), "restore needs your password")
r = cl.post("/backups/restore", data={"csrf": tok, "name": "../budget.db", "current": "second password!"}, follow_redirects=True)
ok("doesn't exist" in html.unescape(r.data.decode()), "restore only takes files from the backups folder")
r = cl.post("/backups/restore", data={"csrf": tok, "name": manual, "current": "second password!"}, follow_redirects=True)
with db.connect() as conn:
    now_n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
ok(now_n == after_delete + 5 and "Restored" in html.unescape(r.data.decode()), "restore brings the deleted transactions back")
ok(any(b["name"].endswith("-before-restore.db") for b in security.backups()), "budget backed up before restoring")
ok(cl.get("/register?month=all").status_code == 200, "Register works after restore")

# the updater and lock.json recovery
ok(isinstance(updater.status(), dict), "updater works without the database")
shutil.copy(security.LOCK_PATH, security.LOCK_PATH + ".keep"); os.remove(security.LOCK_PATH)
ok(security.is_set_up() and os.path.exists(security.LOCK_PATH), "a missing lock.json comes back from the backups folder")

# ---- duplicates: bank copy of something you typed in
with db.connect() as conn:
    linked = conn.execute("INSERT INTO accounts (name, kind, plaid_account_id, sort, auto_opening) VALUES ('T Bank Checking','checking','pa-1',60,0)").lastrowid
    other = conn.execute("INSERT INTO accounts (name, kind, sort, auto_opening) VALUES ('T Cash','cash',61,0)").lastrowid
    cat = conn.execute("SELECT id FROM categories WHERE kind='expense' ORDER BY id LIMIT 1").fetchone()[0]
    d0 = date.today() - timedelta(days=30)
    def bank(acc, d, amount, pid, cleared="c", payee="BANK PAYEE"):
        return conn.execute("INSERT INTO transactions (plaid_id, account_id, date, payee, bank_name, amount, cleared) VALUES (?,?,?,?,?,?,?)",
                            (pid, acc, d.isoformat(), payee, payee, amount, cleared)).lastrowid
    b1 = bank(linked, d0 + timedelta(days=6), -45.67, "pb1", payee="CHECK 1042")
    bp = bank(linked, d0 + timedelta(days=2), -12.34, "pb2", cleared="")          # pending: not combined yet
    bfar = bank(linked, d0 + timedelta(days=20), -99.99, "pb3")                    # 20 days after: too far
r = cl.post("/register/add", data={"csrf": tok, "account_id": str(linked), "date": d0.isoformat(), "payee": "Rent to Sam",
                                   "memo": "March", "payment": "45.67", "category_id": str(cat), "next": "/register"}, follow_redirects=True)
ok("combined" in html.unescape(r.data.decode()), "adding something the bank already sent says it was combined")
with db.connect() as conn:
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (b1,)).fetchone()
    mine_left = conn.execute("SELECT COUNT(*) FROM transactions WHERE account_id = ? AND plaid_id IS NULL AND amount = -45.67", (linked,)).fetchone()[0]
ok(row["payee"] == "Rent to Sam" and row["memo"] == "March" and row["category_id"] == cat and row["merged"], "the bank's copy takes your payee, memo, category")
ok(mine_left == 0, "only one copy is left, so it counts once")
with db.connect() as conn:
    m1 = conn.execute("INSERT INTO transactions (account_id, date, payee, amount, category_locked, payee_locked) VALUES (?,?,?,?,1,1)", (linked, d0.isoformat(), "Coffee", -12.34)).lastrowid
    m2 = conn.execute("INSERT INTO transactions (account_id, date, payee, amount, category_locked, payee_locked) VALUES (?,?,?,?,1,1)", (linked, d0.isoformat(), "Big thing", -99.99)).lastrowid
    m3 = conn.execute("INSERT INTO transactions (account_id, date, payee, amount, category_locked, payee_locked) VALUES (?,?,?,?,1,1)", (other, d0.isoformat(), "Cash", -45.67)).lastrowid
    n = duplicates.match(conn)
ok(n == 0, "pending, too-far-apart and unlinked-account entries aren't combined")
with db.connect() as conn:
    conn.execute("UPDATE transactions SET cleared = 'c' WHERE id = ?", (bp,))
    ok(duplicates.match(conn) == 1, "once the pending charge posts, it's combined")
h = html.unescape(cl.get(f"/register?month=all&account={linked}").data.decode())
ok("combined" in h and "Not the same" in h and "You typed this in on" in h, "Register shows the combined label and Not the same")
r = cl.post(f"/register/{b1}/split", data={"csrf": tok}, follow_redirects=True)
with db.connect() as conn:
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (b1,)).fetchone()
    back = conn.execute("SELECT * FROM transactions WHERE account_id = ? AND plaid_id IS NULL AND amount = -45.67", (linked,)).fetchone()
    ok(row["payee"] == "CHECK 1042" and row["category_id"] is None and not row["merged"] and back and back["payee"] == "Rent to Sam"
       and back["category_id"] == cat, "Not the same: your entry and the bank's both come back as they were")
    ok(duplicates.match(conn) == 0, "...and they aren't combined again")
    # the bank withdraws a combined transaction: your entry comes back
    combined_row = conn.execute("SELECT * FROM transactions WHERE plaid_id = 'pb2'").fetchone()
    duplicates.bank_removed(conn, ["pb2"]); conn.execute("DELETE FROM transactions WHERE plaid_id = 'pb2'")
    ok(conn.execute("SELECT COUNT(*) FROM transactions WHERE payee = 'Coffee' AND plaid_id IS NULL").fetchone()[0] == 1,
       "if the bank withdraws it, your own entry comes back")
    db.set_setting(conn, "combined_unseen", "2")
h = html.unescape(cl.get("/").data.decode())
ok("combined with" in h and "combined=1" in h, "To do card points out combined transactions")
cl.get("/register?month=all&combined=1")
with db.connect() as conn:
    ok(db.get_setting(conn, "combined_unseen") == "0", "looking at them clears the To do item")
for p in ("/", "/register", "/reports", "/accounts", "/budget", "/export", "/categories", "/settings"):
    ok(cl.get(p).status_code == 200, f"{p} loads")
print("\n" + ("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
