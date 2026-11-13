"""Synthetic data only: bank sync runs once when the app opens; no Sync buttons or repeating sync."""
import os, shutil, sys, threading, time
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
import app as A, db, plaid_client as plaid, updater
with db.connect() as conn:
    db.set_setting(conn, "plaid_client_id", "x"); db.set_setting(conn, "plaid_secret", "y")
    conn.execute("INSERT OR IGNORE INTO items (item_id, access_token, institution_name) VALUES ('it1', 'tok', 'Fake Bank')")
calls = []
gate = threading.Event()
def fake_sync_all():
    calls.append(time.time()); gate.wait(5)
    return {"added": 2, "modified": 0, "removed": 0}, []
plaid.sync_all = fake_sync_all
updater.check = lambda: None
ok(plaid.configured(), "fake Plaid keys count as set up")

A.LOCK['started'] = False; A.lock_now()
with A.app.test_request_context(): A.finish_unlock(unlock_for_tests.KEY)  # sync starts on unlock
cl = A.app.test_client(); cl.get("/?key=" + A.LAUNCH_KEY)
time.sleep(0.3)
ok(len(calls) == 1, "one sync starts when the app opens")
h = cl.get("/").data.decode()
ok("Syncing with your bank" in h, "the window says it's syncing")
ok(cl.get("/api/sync-status").json["running"] is True, "status: running")
gate.set(); time.sleep(0.3)
st = cl.get("/api/sync-status").json
ok(st["running"] is False and st["changed"] == 2, "status: done, 2 changed (the page reloads)")
h = cl.get("/").data.decode()
ok("Syncing with your bank" not in h and "Bank sync:" in h, "after syncing: no label, last sync time shown")
for page in ("/", "/accounts", "/settings"):
    html = cl.get(page).data.decode()
    ok("Sync now" not in html and "Sync banks" not in html and "Auto-sync" not in html, f"{page}: no sync button or interval")
ok(cl.post("/sync").status_code in (400, 404, 405), "the old /sync action is gone")
time.sleep(0.5)
ok(len(calls) == 1, "no further syncs while open")
with A.app.test_request_context():
    pass
ok("sync_interval_hours" not in plaid.CONFIG_DEFAULTS if hasattr(plaid, "CONFIG_DEFAULTS") else True, "interval setting removed")
print("\n" + ("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
