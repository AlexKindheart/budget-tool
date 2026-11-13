"""Synthetic data only: the optional password (no password, add one later, remove it again)."""
import html, json, os, re, shutil, sys, time
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
import app as A, db, security
cl = A.app.test_client(); cl.get("/?key=" + A.LAUNCH_KEY)
page = lambda r: html.unescape(r.data.decode())
h = page(cl.get("/setup"))
with cl.session_transaction() as s: tok = s["csrf"]
ok("Continue without a password" in h and "Banking information in the wrong hands is a bad idea" in h, "setup offers no password, with the warning")
r = cl.post("/setup", data={"csrf": tok, "action": "none"}, follow_redirects=True)
ok("Tick the box" in page(r) and not security.is_set_up(), "skipping needs the 'I understand' tick")
r = cl.post("/setup", data={"csrf": tok, "action": "none", "understand": "1"}, follow_redirects=True)
ok(db.unlocked() and r.status_code == 200 and "opens without a password" in page(r), "continue without a password")
ok("open" in json.load(open(security.LOCK_PATH)) and not security.has_password(), "lock.json: no password")
h = page(cl.get("/"))
ok('href="/lock"' not in h and "data-lock-after" not in h, "no Lock button or auto-lock without a password")
A.lock_now()  # like closing and reopening Budget Tool
ok(cl.get("/budget").status_code == 200 and db.unlocked(), "reopening: opens straight away, no password screen")
A.LOCK["last_active"] = time.time() - 10 * 3600
ok(cl.get("/budget").status_code == 200 and db.unlocked(), "never locks itself without a password")
r = cl.get("/lock", follow_redirects=True)
ok(db.unlocked() and "can't lock" in page(r), "Lock explains it needs a password")
h = page(cl.get("/settings"))
ok("Your budget has no password" in h and "bad idea" in h and "Set a password" in h, "Settings warns and offers a password")
r = cl.post("/backups/now", data={"csrf": tok}); manual = [b for b in security.backups() if b["name"].endswith("-manual.db")][0]["name"]
r = cl.post("/backups/restore", data={"csrf": tok, "name": manual}, follow_redirects=True)
ok("Restored" in page(r), "restore works without a password")
# add a password later
r = cl.post("/settings/password", data={"csrf": tok, "password": "maple tractor sunset", "confirm": "maple tractor sunset"})
m = re.search(r'recovery-code">([A-Z2-7-]+)<', page(r)); CODE = m.group(1) if m else ""
ok(CODE and security.has_password() and "open" not in json.load(open(security.LOCK_PATH)), "adding a password: recovery code shown, key no longer stored openly")
h = page(cl.get("/"))
ok('href="/lock"' in h and 'data-lock-after="15"' in h, "Lock button and auto-lock now on")
cl.get("/lock"); ok(not db.unlocked() and cl.get("/budget").status_code == 302, "now it locks and needs the password")
cl.post("/unlock", data={"csrf": tok, "password": "maple tractor sunset"}); ok(db.unlocked(), "password unlocks")
ok(len(security.unlock_with_code(CODE)) == 32, "recovery code works")
r = cl.post("/backups/restore", data={"csrf": tok, "name": manual}, follow_redirects=True)
ok("isn't right" in page(r), "with a password, restore asks for it again")
# remove it again
r = cl.post("/settings/remove-password", data={"csrf": tok, "current": "maple tractor sunset"}, follow_redirects=True)
ok("Tick the box" in page(r) and security.has_password(), "removing needs the 'I understand' tick")
r = cl.post("/settings/remove-password", data={"csrf": tok, "current": "wrong wrong wrong", "understand": "1"}, follow_redirects=True)
ok("isn't right" in page(r) and security.has_password(), "removing needs the right password")
h = page(cl.get("/settings")); ok("Stop using a password" in h and "bad idea" in h, "Settings warns before removing")
r = cl.post("/settings/remove-password", data={"csrf": tok, "current": "maple tractor sunset", "understand": "1"}, follow_redirects=True)
ok(not security.has_password() and "isn't protected" in page(r), "password removed")
try:
    security.unlock_with_code(CODE); ok(False, "old recovery code no longer applies")
except security.WrongPassword:
    ok(True, "old recovery code no longer applies")
A.lock_now(); ok(cl.get("/").status_code == 200, "opens without a password again")
for b in security.backups():
    db.verify(os.path.join(security.BACKUP_DIR, b["name"]))
ok(True, "every backup still opens")
print("\n" + ("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
