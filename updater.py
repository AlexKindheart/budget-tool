r"""Updates: every commit pushed to the main branch on GitHub is an update (no releases needed).

Checking asks GitHub for the newest commit on main and compares it with the commit this copy was built from
(app\COMMIT). Installing (done by "Update Budget Tool.exe", see update_gui.py):
  1. downloads exactly that commit's code from GitHub (fixed address, HTTPS),
  2. copies only Budget Tool's own files (code, templates, static, VERSION, requirements.txt) into a new folder,
  3. installs any new libraries if requirements.txt changed (into the private runtime\),
  4. checks every .py file compiles,
  5. swaps the new app\ in, keeping the previous one as app.previous (put back automatically if the swap fails).
Only app\ (and, when needed, libraries in runtime\) change; the data folder is never touched.
Running from source (no app\COMMIT) never updates itself: use git pull.
"""
import io
import json
import os
import py_compile
import re
import shutil
import subprocess
import time
import zipfile
from datetime import datetime, timedelta, timezone

import requests

import db
import paths

REPO = "AlexKindheart/budget-tool"
BRANCH = "main"
API = f"https://api.github.com/repos/{REPO}"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "BudgetTool-updater"}
CHECK_EVERY = timedelta(hours=6)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_DOWNLOAD = 100 * 1024 * 1024
APP_DIRS = ("templates", "static")                  # copied whole
APP_FILES = ("VERSION", "requirements.txt")          # plus every top-level .py file
MUST_HAVE = ("app.py", "budget_tool.py", "update_gui.py", "updater.py", "paths.py", "VERSION",
             os.path.join("templates", "base.html"), os.path.join("static", "style.css"))
CREATE_NO_WINDOW = 0x08000000


# ---------------------------------------------------------------- what's installed

def current_version():
    return version_of(paths.SOURCE_DIR)


def version_of(folder):
    try:
        with open(os.path.join(folder, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "0.0.0"


def installed_commit():
    """The commit this copy was built from, or None (running from source)."""
    try:
        with open(os.path.join(paths.SOURCE_DIR, "COMMIT"), encoding="utf-8") as f:
            sha = f.read().strip()
        return sha if SHA_RE.match(sha) else None
    except OSError:
        return None


def label(version, sha):
    return f"{version} ({sha[:7]})" if sha else version


def can_install():
    return bool(paths.INSTALLED and installed_commit())


# ---------------------------------------------------------------- checking

def cached():
    with db.connect() as conn:
        raw = db.get_setting(conn, "update_check")
    try:
        info = json.loads(raw) if raw else None
    except ValueError:
        return None
    return info if info and info.get("kind") == "commits" else None  # ignore answers from the old release updater


def status():
    """What the screens show, from the last check (no network)."""
    info = cached() or {}
    mine = installed_commit()
    latest = info.get("latest_sha")
    msgs = info.get("messages") or []
    return {
        "current": label(current_version(), mine),
        "latest": label(info.get("latest_version") or "", latest) if latest else None,
        "latest_sha": latest,
        "available": bool(can_install() and latest and latest != mine and info.get("ahead")),
        "count": info.get("count", 0),
        "notes": "\n".join("• " + m for m in msgs),
        "messages": msgs,
        "checked_at": info.get("checked_at"),
        "error": info.get("error"),
        "can_install": can_install(),
        "from_source": not paths.INSTALLED,
    }


def check(force=False):
    """Ask GitHub for the newest commit on main (at most every 6 hours unless forced). Returns status()."""
    info = cached()
    if info and not force and info.get("checked_at"):
        try:
            if datetime.now(timezone.utc) - datetime.fromisoformat(info["checked_at"]) < CHECK_EVERY:
                return status()
        except ValueError:
            pass
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        head = requests.get(f"{API}/commits/{BRANCH}", headers=HEADERS, timeout=20)
        head.raise_for_status()
        head = head.json()
        latest = head["sha"]
        if not SHA_RE.match(latest):
            raise RuntimeError("GitHub sent an unexpected answer")
        mine = installed_commit()
        ahead, messages, count = latest != mine, [], 0
        if mine and ahead:
            cmp = requests.get(f"{API}/compare/{mine}...{latest}", headers=HEADERS, timeout=20)
            if cmp.ok:
                data = cmp.json()
                ahead = data.get("status") in ("ahead", "diverged")
                count = data.get("ahead_by", 0)
                messages = [c["commit"]["message"].splitlines()[0] for c in reversed(data.get("commits", []))]
            else:  # this copy was built from a commit GitHub doesn't have: offer the latest anyway
                count = 1
                messages = [head["commit"]["message"].splitlines()[0]]
        version = requests.get(f"https://raw.githubusercontent.com/{REPO}/{latest}/VERSION", headers=HEADERS, timeout=20)
        info = {"kind": "commits", "checked_at": now, "latest_sha": latest, "ahead": ahead, "count": count,
                "latest_version": version.text.strip()[:40] if version.ok else current_version(),
                "messages": messages[:60], "error": None}
    except Exception as e:  # offline, rate-limited, etc. Keep the last good answer.
        info = dict(info or {"kind": "commits"})
        info.update(checked_at=now, error=f"Couldn't check for updates: {e}")
    with db.connect() as conn:
        db.set_setting(conn, "update_check", json.dumps(info))
    return status()


# ---------------------------------------------------------------- is Budget Tool open?

def pid_file():
    return os.path.join(paths.DATA_DIR, "app.pid")


def _alive(pid):
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    except Exception:
        return False


def app_running():
    try:
        with open(pid_file(), encoding="utf-8") as f:
            pid = int(f.read().strip() or 0)
    except (OSError, ValueError):
        return False
    return pid != os.getpid() and _alive(pid)


def wait_for_exit(pid=None, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        if not (_alive(pid) if pid else app_running()):
            return True
        time.sleep(0.5)
    return False


def launch_updater():
    """From inside Budget Tool: start the updater, which waits for this app to close, installs, and reopens it."""
    exe = os.path.join(paths.APP_DIR, "Update Budget Tool.exe")
    if not os.path.isfile(exe):
        raise RuntimeError("The updater (Update Budget Tool.exe) is missing. Install Budget Tool into this folder again.")
    subprocess.Popen([exe, "--auto", "--relaunch", "--pid", str(os.getpid())], cwd=paths.APP_DIR, close_fds=True)


# ---------------------------------------------------------------- installing

def _log(msg):
    try:
        with open(os.path.join(paths.DATA_DIR, "update.log"), "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
    except OSError:
        pass


def _download(sha, into):
    """This commit's code from GitHub, unpacked safely into `into`. Returns the folder holding the code."""
    if not SHA_RE.match(sha or ""):
        raise RuntimeError("That isn't a valid commit id.")
    url = f"https://codeload.github.com/{REPO}/zip/{sha}"  # fixed address; only the commit id varies
    resp = requests.get(url, headers=HEADERS, timeout=60, stream=True)
    resp.raise_for_status()
    buf = io.BytesIO()
    for chunk in resp.iter_content(1 << 16):
        buf.write(chunk)
        if buf.tell() > MAX_DOWNLOAD:
            raise RuntimeError("The download is far bigger than expected. Not installing it.")
    os.makedirs(into, exist_ok=True)
    root = os.path.realpath(into)
    with zipfile.ZipFile(buf) as z:
        for name in z.namelist():
            target = os.path.realpath(os.path.join(into, name))
            if not target.startswith(root + os.sep):
                raise RuntimeError("The download looks wrong (a file points outside its folder). Not installing it.")
        z.extractall(into)
    for entry in os.listdir(into):
        folder = os.path.join(into, entry)
        if os.path.isdir(folder) and os.path.isfile(os.path.join(folder, "app.py")):
            return folder
    raise RuntimeError("The download doesn't contain Budget Tool. Not installing it.")


def copy_app_files(src, dst, sha=None):
    """Only Budget Tool's own files: top-level .py, VERSION, requirements.txt, templates/, static/."""
    os.makedirs(dst)
    for name in os.listdir(src):
        path = os.path.join(src, name)
        if os.path.isfile(path) and (name.endswith(".py") or name in APP_FILES):
            shutil.copy2(path, os.path.join(dst, name))
    for d in APP_DIRS:
        if os.path.isdir(os.path.join(src, d)):
            shutil.copytree(os.path.join(src, d), os.path.join(dst, d), ignore=shutil.ignore_patterns("__pycache__"))
    if sha:
        with open(os.path.join(dst, "COMMIT"), "w", encoding="utf-8") as f:
            f.write(sha + "\n")


def _verify(folder):
    missing = [m for m in MUST_HAVE if not os.path.exists(os.path.join(folder, m))]
    if missing:
        raise RuntimeError("The update is missing " + ", ".join(missing) + ". Not installing it.")
    for name in os.listdir(folder):
        if name.endswith(".py"):
            try:
                py_compile.compile(os.path.join(folder, name), doraise=True,
                                   cfile=os.path.join(folder, "__pycache__", name + "c"))
            except py_compile.PyCompileError as e:
                raise RuntimeError(f"The update has a problem in {name}, so it wasn't installed: {e.msg.strip()[:300]}")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return "\n".join(line.strip() for line in f if line.strip() and not line.strip().startswith("#"))
    except OSError:
        return ""


def _install_libraries(requirements):
    python = os.path.join(paths.RUNTIME_DIR, "python.exe")
    r = subprocess.run([python, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
                        "-q", "-r", requirements], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
                       timeout=900)
    if r.returncode != 0:
        raise RuntimeError("Couldn't install the libraries this update needs:\n" + (r.stderr or r.stdout)[-600:])


def install(sha, progress=lambda msg: None):
    """Download, check and install commit `sha`. Raises with a friendly message if anything is wrong; the
    installed app is only replaced at the very end, after every check passed."""
    if not can_install():
        raise RuntimeError("This copy runs from source, so it doesn't update itself. Use git pull instead.")
    if app_running():
        raise RuntimeError("Close Budget Tool first, then click Update now again.")
    app_dir = os.path.join(paths.APP_DIR, "app")
    work = os.path.join(paths.APP_DIR, ".update")  # same drive as app\, so the final swap is a quick rename
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    try:
        progress("Downloading the update from GitHub…")
        src = _download(sha, os.path.join(work, "download"))
        progress("Checking it…")
        new_app = os.path.join(work, "app")
        copy_app_files(src, new_app, sha)
        _verify(new_app)
        if _read(os.path.join(new_app, "requirements.txt")) != _read(os.path.join(app_dir, "requirements.txt")):
            progress("Installing the libraries it needs (this can take a minute)…")
            _install_libraries(os.path.join(new_app, "requirements.txt"))
        progress("Installing…")
        previous = os.path.join(paths.APP_DIR, "app.previous")
        shutil.rmtree(previous, ignore_errors=True)
        os.rename(app_dir, previous)
        try:
            os.rename(new_app, app_dir)
        except OSError:
            os.rename(previous, app_dir)  # put the old version back
            raise
        _log(f"Updated to {sha} ({version_of(app_dir)})")
    except Exception as e:
        _log(f"Update to {sha} failed: {e}")
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def roll_back():
    """Put the previous version (app.previous) back."""
    app_dir = os.path.join(paths.APP_DIR, "app")
    previous = os.path.join(paths.APP_DIR, "app.previous")
    if not os.path.isdir(previous):
        raise RuntimeError("There's no previous version to go back to.")
    if app_running():
        raise RuntimeError("Close Budget Tool first.")
    broken = os.path.join(paths.APP_DIR, "app.rolled-back")
    shutil.rmtree(broken, ignore_errors=True)
    os.rename(app_dir, broken)
    os.rename(previous, app_dir)
    shutil.rmtree(broken, ignore_errors=True)
    _log("Rolled back to the previous version")
