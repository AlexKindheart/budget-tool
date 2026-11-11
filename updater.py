"""Self-updater. Checks this app's GitHub releases and installs a newer version.

Only ever downloads from REPO's releases, over HTTPS, and only installs a zip whose SHA-256
matches the checksum published with the release. Your data (%APPDATA%\\BudgetTool) is never touched;
only the program folder is replaced, and the old one is restored if copying fails.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone

import requests

import db
from paths import DATA_DIR, RESOURCE_DIR

REPO = "AlexKindheart/budget-tool"
ASSET = "BudgetTool-windows.zip"
CHECK_EVERY = timedelta(hours=6)
API = f"https://api.github.com/repos/{REPO}/releases/latest"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "BudgetTool-updater"}


def current_version():
    try:
        with open(os.path.join(RESOURCE_DIR, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "0.0.0"


def parse_version(v):
    """'v1.10.2' -> (1, 10, 2). Anything unparseable sorts lowest."""
    m = re.match(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", (v or "").strip())
    return tuple(int(x or 0) for x in m.groups()) if m else (0, 0, 0)


def is_installed_app():
    """True when running as the built .exe (updates can't replace a source checkout)."""
    return bool(getattr(sys, "frozen", False))


def install_dir():
    return os.path.dirname(sys.executable)


def cached():
    with db.connect() as conn:
        raw = db.get_setting(conn, "update_check")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def status():
    """What the screens show, from the last check (no network)."""
    info = cached() or {}
    latest = info.get("latest")
    return {
        "current": current_version(),
        "latest": latest,
        "available": bool(latest and info.get("url") and parse_version(latest) > parse_version(current_version())),
        "checked_at": info.get("checked_at"),
        "notes": info.get("notes") or "",
        "page": info.get("page"),
        "error": info.get("error"),
        "can_install": is_installed_app(),
    }


def check(force=False):
    """Ask GitHub for the latest release (at most every 6 hours unless forced). Returns status()."""
    info = cached()
    if info and not force and info.get("checked_at"):
        try:
            if datetime.now(timezone.utc) - datetime.fromisoformat(info["checked_at"]) < CHECK_EVERY:
                return status()
        except ValueError:
            pass
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        resp = requests.get(API, headers=HEADERS, timeout=20)
        if resp.status_code == 404:
            info = {"checked_at": now, "latest": None, "error": None}
        else:
            resp.raise_for_status()
            rel = resp.json()
            assets = {a["name"]: a for a in rel.get("assets", [])}
            zip_asset = assets.get(ASSET)
            sum_asset = assets.get(ASSET + ".sha256")
            info = {
                "checked_at": now,
                "latest": rel.get("tag_name"),
                "notes": (rel.get("body") or "")[:4000],
                "page": rel.get("html_url"),
                "url": zip_asset["browser_download_url"] if zip_asset else None,
                "size": zip_asset.get("size") if zip_asset else None,
                # GitHub publishes "sha256:<hex>" for each asset; the .sha256 file is the fallback.
                "digest": (zip_asset or {}).get("digest"),
                "sum_url": sum_asset["browser_download_url"] if sum_asset else None,
                "error": None,
            }
    except Exception as e:  # offline, rate-limited, etc. Keep the last good answer.
        info = dict(info or {})
        info.update(checked_at=now, error=f"Couldn't check for updates: {e}")
    with db.connect() as conn:
        db.set_setting(conn, "update_check", json.dumps(info))
    return status()


def _expected_sha256(info):
    digest = info.get("digest") or ""
    if digest.startswith("sha256:"):
        return digest.split(":", 1)[1].lower()
    if _is_trusted_url(info.get("sum_url")):
        text = requests.get(info["sum_url"], headers=HEADERS, timeout=20).text
        m = re.search(r"\b([0-9a-fA-F]{64})\b", text)
        if m:
            return m.group(1).lower()
    raise RuntimeError("The release has no checksum, so it can't be verified. Not installing it.")


def _is_trusted_url(url):
    """Release downloads start at github.com/<this repo>/releases/download/ (GitHub then redirects to its CDN)."""
    return bool(url) and url.startswith(f"https://github.com/{REPO}/releases/download/")


def prepare():
    """Download, verify and unpack the latest release. Returns the folder holding the new BudgetTool.exe."""
    if not is_installed_app():
        raise RuntimeError("Updates install into the built app. You're running from source, so use git pull instead.")
    info = cached() or {}
    if not status()["available"]:
        raise RuntimeError("You already have the latest version.")
    url = info.get("url")
    if not _is_trusted_url(url):
        raise RuntimeError("The download link isn't from this app's GitHub releases. Not installing it.")
    expected = _expected_sha256(info)

    work = tempfile.mkdtemp(prefix="BudgetTool-update-")
    try:
        return _download_and_unpack(url, expected, work), work
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise


def _download_and_unpack(url, expected, work):
    pkg = os.path.join(work, ASSET)
    sha = hashlib.sha256()
    with requests.get(url, headers=HEADERS, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(pkg, "wb") as f:
            for chunk in r.iter_content(1024 * 256):
                f.write(chunk)
                sha.update(chunk)
    if sha.hexdigest().lower() != expected:
        raise RuntimeError("The download didn't match its published checksum, so it wasn't installed. Try again later.")

    staging = os.path.join(work, "new")
    with zipfile.ZipFile(pkg) as z:
        for name in z.namelist():  # refuse anything that would land outside the staging folder
            target = os.path.realpath(os.path.join(staging, name))
            if not target.startswith(os.path.realpath(staging) + os.sep):
                raise RuntimeError("The update package looks wrong. Not installing it.")
        z.extractall(staging)
    for dirpath, _dirs, files in os.walk(staging):
        if "BudgetTool.exe" in files and os.path.isdir(os.path.join(dirpath, "_internal")):
            return dirpath
    raise RuntimeError("The update package doesn't contain BudgetTool.exe. Not installing it.")


SWAP_SCRIPT = r"""
param([int]$AppPid, [string]$Src, [string]$Dst, [string]$Work, [string]$Log)
$ErrorActionPreference = 'Continue'
function Note($m) { Add-Content -Path $Log -Value ("{0}  {1}" -f (Get-Date -Format s), $m) }
Note "Update: waiting for Budget Tool (pid $AppPid) to close"
try { Wait-Process -Id $AppPid -Timeout 60 -ErrorAction Stop } catch { }
Start-Sleep -Milliseconds 800
$backup = Join-Path $Work 'previous'
robocopy $Dst $backup /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Note "Couldn't back up the current version (robocopy $LASTEXITCODE); update cancelled"; Start-Process (Join-Path $Dst 'BudgetTool.exe'); exit 1 }
robocopy $Src $Dst /MIR /R:5 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) {
  Note "Copying the new version failed (robocopy $LASTEXITCODE); restoring the previous version"
  robocopy $backup $Dst /MIR /R:5 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
} else { Note "Update installed" }
Start-Process (Join-Path $Dst 'BudgetTool.exe')
Start-Sleep -Seconds 2
Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
"""


def launch_swap(new_dir, work):
    """Start the helper that replaces the program folder once this app has exited, then relaunches it."""
    dst = install_dir()
    if not os.path.isfile(os.path.join(dst, "BudgetTool.exe")) or not os.path.isdir(os.path.join(dst, "_internal")):
        raise RuntimeError(f"Unexpected install folder ({dst}); not updating it automatically.")
    script = os.path.join(work, "swap.ps1")
    with open(script, "w", encoding="utf-8-sig") as f:
        f.write(SWAP_SCRIPT)
    args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", script,
            "-AppPid", str(os.getpid()), "-Src", new_dir, "-Dst", dst, "-Work", work,
            "-Log", os.path.join(DATA_DIR, "update.log")]
    # CREATE_NO_WINDOW gives PowerShell a hidden console (it won't run fully detached) | CREATE_NEW_PROCESS_GROUP
    flags = 0x08000000 | 0x00000200
    try:
        # Break away from any job object the app was started in, so the helper outlives the app.
        subprocess.Popen(args, creationflags=flags | 0x01000000, close_fds=True)  # CREATE_BREAKAWAY_FROM_JOB
    except OSError:
        subprocess.Popen(args, creationflags=flags, close_fds=True)
