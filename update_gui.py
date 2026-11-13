"""The updater window ("Update Budget Tool.exe" in the Budget Tool folder).

Opened by itself: shows your version, checks GitHub for newer commits, lists what changed, and installs on
"Update now". Opened by Budget Tool's update banner (--auto --relaunch --pid N): waits for Budget Tool to close,
installs, and opens Budget Tool again. The work itself is in updater.py.
"""
import argparse
import json
import os
import subprocess
import sys
import threading

# The private Python only searches the folders listed in its settings, so make sure this file's folder (app\) is one.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import webview  # noqa: E402

import paths  # noqa: E402
import updater  # noqa: E402

PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><style>
  :root { color-scheme: dark; }
  body { margin: 0; background: #1f1f21; color: #e4e1d6; font: 15px/1.5 "Segoe UI", system-ui, sans-serif; }
  header { background: #151516; border-bottom: 4px solid #eede75; padding: 12px 20px;
           font: 700 20px "Segoe UI", sans-serif; letter-spacing: .04em; text-transform: uppercase; }
  header span { color: #eede75; }
  main { padding: 18px 20px; }
  .muted { color: #879099; } .small { font-size: 13px; }
  #status { margin: 8px 0 12px; font-size: 16px; }
  #status.ok { color: #8fd694; } #status.err { color: #f2837a; white-space: pre-wrap; }
  ul { margin: 6px 0 14px; padding-left: 20px; max-height: 170px; overflow: auto; }
  button { font: 600 14px "Segoe UI", sans-serif; text-transform: uppercase; letter-spacing: .06em; padding: 9px 18px;
           border-radius: 3px; cursor: pointer; border: 2px solid #879099; background: transparent; color: #e4e1d6; margin-right: 8px; }
  button.primary { background: #eede75; border-color: #eede75; color: #26262a; }
  button[disabled] { opacity: .5; cursor: wait; }
  a { color: #64a6e8; cursor: pointer; }
</style></head><body>
<header>Update Budget <span>Tool</span></header>
<main>
  <div class="muted small">You have <strong id="current">…</strong></div>
  <div id="status">Starting…</div>
  <div id="whatsnew" hidden><div class="muted small">What's new:</div><ul id="notes"></ul></div>
  <div id="buttons"></div>
  <p class="small muted" id="rollback" hidden>Something wrong after an update? <a onclick="pywebview.api.roll_back()">Go back to the previous version</a></p>
</main>
<script>
  function esc(t) { return String(t).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
  function setStatus(msg, kind) { const s = document.getElementById('status'); s.textContent = msg; s.className = kind || ''; }
  function show(st) {
    document.getElementById('current').textContent = st.current;
    document.getElementById('rollback').hidden = !st.has_previous;
    const notes = document.getElementById('notes');
    notes.innerHTML = (st.messages || []).map(m => '<li>' + esc(m) + '</li>').join('');
    document.getElementById('whatsnew').hidden = !(st.available && st.messages && st.messages.length);
    const b = document.getElementById('buttons'); b.innerHTML = '';
    if (st.available) {
      setStatus('An update is available: ' + st.latest + (st.count > 1 ? ' (' + st.count + ' changes)' : ''));
      b.innerHTML = '<button class="primary" onclick="install(this)">Update now</button><button onclick="pywebview.api.close()">Later</button>';
    } else if (st.error) {
      setStatus(st.error, 'err');
      b.innerHTML = '<button onclick="check(this)">Try again</button><button onclick="pywebview.api.close()">Close</button>';
    } else if (st.from_source) {
      setStatus("This copy runs from source, so it doesn't update itself. Use git pull.");
      b.innerHTML = '<button onclick="pywebview.api.close()">Close</button>';
    } else if (st.checked) {
      setStatus("You're up to date.", 'ok');
      b.innerHTML = '<button onclick="check(this)">Check again</button><button onclick="pywebview.api.close()">Close</button>';
    }
  }
  function check(btn) { if (btn) btn.disabled = true; setStatus('Checking GitHub for updates…'); pywebview.api.check().then(show); }
  function install(btn) {
    document.querySelectorAll('button').forEach(x => x.disabled = true);
    pywebview.api.install();
  }
  function progress(msg) { setStatus(msg); }
  function finished(ok, msg, canOpen) {
    setStatus(msg, ok ? 'ok' : 'err');
    const b = document.getElementById('buttons');
    b.innerHTML = (canOpen ? '<button class="primary" onclick="pywebview.api.open_app()">Open Budget Tool</button>' : '')
      + (ok ? '' : '<button onclick="check(this)">Try again</button>') + '<button onclick="pywebview.api.close()">Close</button>';
  }
  window.addEventListener('pywebviewready', () => pywebview.api.ready().then(show));
</script></body></html>"""


class Api:
    def __init__(self, args):
        self._args = args
        self._window = None
        self._busy = False

    def _status(self, checked=False):
        st = updater.status()
        st["checked"] = checked
        st["has_previous"] = updater.can_roll_back()
        return st

    def _js(self, fn, *args):
        self._window.evaluate_js(f"{fn}({', '.join(json.dumps(a) for a in args)})")

    def ready(self):
        if self._args.auto:
            threading.Thread(target=self._auto, daemon=True).start()
            st = self._status()
            st["available"] = False
            return st
        return self.check()

    def check(self):
        updater.check(force=True)
        return self._status(checked=True)

    def install(self):
        if not self._busy:
            self._busy = True
            threading.Thread(target=self._install, daemon=True).start()

    def _auto(self):
        if self._args.pid:
            self._js("progress", "Waiting for Budget Tool to close…")
            updater.wait_for_exit(self._args.pid, 60)
        self._js("progress", "Checking GitHub for updates…")
        st = updater.check(force=True)
        if not st["available"]:
            self._js("finished", True, "You're up to date." if not st["error"] else st["error"], True)
            return
        self._install()

    def _install(self):
        st = updater.status()
        try:
            if not updater.wait_for_exit(None, 20):
                raise RuntimeError("Close Budget Tool first, then click Update now again.")
            updater.install(st["latest_sha"], lambda msg: self._js("progress", msg))
        except Exception as e:
            self._busy = False
            self._js("finished", False, f"The update wasn't installed. Your current version still works.\n\n{e}", True)
            return
        if self._args.relaunch:
            self._js("finished", True, "Updated! Opening Budget Tool…", False)
            self.open_app()
        else:
            self._js("finished", True, f"Updated to {st['latest']}.", True)

    def roll_back(self):
        try:
            updater.roll_back()
            self._js("finished", True, "Back on the previous version.", True)
        except Exception as e:
            self._js("finished", False, str(e), True)

    def open_app(self):
        exe = os.path.join(paths.APP_DIR, "BudgetTool.exe")
        if os.path.isfile(exe):
            subprocess.Popen([exe], cwd=paths.APP_DIR, close_fds=True)
        self.close()

    def close(self):
        self._window.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto", action="store_true", help="install without asking (from Budget Tool's banner)")
    parser.add_argument("--relaunch", action="store_true", help="open Budget Tool afterwards")
    parser.add_argument("--pid", type=int, default=0, help="wait for this Budget Tool process to close first")
    args, _ = parser.parse_known_args()
    api = Api(args)
    api._window = webview.create_window("Update Budget Tool", html=PAGE, js_api=api, width=560, height=470,
                                       resizable=False)
    try:  # show Budget Tool's icon on the taskbar, not Python's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AlexKindheart.BudgetTool.Updater")
    except Exception:
        pass
    icon = os.path.join(paths.RESOURCE_DIR, "static", "icon.ico")
    webview.start(private_mode=True, icon=icon if os.path.isfile(icon) else None)


if __name__ == "__main__":
    main()
