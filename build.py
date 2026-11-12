r"""Builds the Budget Tool folder (see paths.py for the layout):

    py build.py                     build into %LOCALAPPDATA%\Programs\BudgetTool
    py build.py E:\BudgetTool       build into another folder (e.g. a USB drive)
    py build.py --stage-only        just make dist\BudgetTool (build-installer.bat packs that)

Steps: a private Python 3.11 (official "embeddable" package from python.org, checksum-verified, cached in .cache\)
in runtime\, the libraries from requirements.txt installed into it, Budget Tool's code in app\ (with app\COMMIT =
the git commit it was built from, which the updater compares with GitHub), and the two launchers compiled with
the C# compiler that comes with Windows. Building into a folder replaces runtime\, app\ and the launchers, and
never touches data\.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("BUDGETTOOL_DATA", os.path.join(HERE, ".cache", "build-data"))  # paths.py needs somewhere to write

PYTHON_VERSION = "3.11.9"
EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
EMBED_SHA256 = "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"
CSC = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Microsoft.NET", "Framework64", "v4.0.30319", "csc.exe")
CACHE = os.path.join(HERE, ".cache")
STAGE = os.path.join(HERE, "dist", "BudgetTool")
LAUNCHERS = {"BudgetTool.exe": [], "Update Budget Tool.exe": ["/define:UPDATER"]}


def say(msg):
    print(msg, flush=True)


def fail(msg):
    print("\nBuild failed: " + msg, flush=True)
    sys.exit(1)


def embedded_python():
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, os.path.basename(EMBED_URL))
    if not os.path.exists(path):
        say(f"Downloading Python {PYTHON_VERSION} (embeddable) from python.org...")
        urllib.request.urlretrieve(EMBED_URL, path + ".part")
        os.replace(path + ".part", path)
    with open(path, "rb") as f:
        if hashlib.sha256(f.read()).hexdigest() != EMBED_SHA256:
            os.remove(path)
            fail("the Python download didn't match its expected checksum (deleted it; run the build again).")
    return path


def build_runtime(dest):
    if sys.version_info[:2] != (3, 11):
        fail(f"build with Python 3.11 (the libraries must match the bundled Python {PYTHON_VERSION}). Try: py -3.11 build.py")
    with zipfile.ZipFile(embedded_python()) as z:
        z.extractall(dest)
    pth = os.path.join(dest, "python311._pth")
    with open(pth, "w", encoding="utf-8") as f:  # where it looks: its own files, the libraries, and Budget Tool's code
        f.write("python311.zip\n.\nLib\\site-packages\n..\\app\nimport site\n")
    site = os.path.join(dest, "Lib", "site-packages")
    say("Installing libraries into the private Python (takes a minute)...")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "--target", site,
                        "-r", os.path.join(HERE, "requirements.txt"), "pip"])
    if r.returncode:
        fail("installing the libraries failed (see above).")


def build_app(dest):
    import updater  # copy exactly what an update copies
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True).stdout.strip()
    updater.copy_app_files(HERE, dest, sha if updater.SHA_RE.match(sha) else None)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=HERE, capture_output=True, text=True).stdout.strip()
    if dirty:
        say("Note: this build includes changes that aren't committed yet.")
    return sha


def build_launchers(dest):
    if not os.path.exists(CSC):
        fail(f"the C# compiler that comes with Windows wasn't found at {CSC}.")
    src = os.path.join(HERE, "launcher", "Launcher.cs")
    for name, extra in LAUNCHERS.items():
        icon = os.path.join(HERE, "static", "icon.ico")
        r = subprocess.run([CSC, "/nologo", "/target:winexe", "/optimize+", "/r:System.Windows.Forms.dll",
                            f"/win32icon:{icon}", f"/out:{os.path.join(dest, name)}", *extra, src],
                           capture_output=True, text=True)
        if r.returncode:
            fail(f"compiling {name} failed:\n{r.stdout}{r.stderr}")


def stage():
    shutil.rmtree(STAGE, ignore_errors=True)
    os.makedirs(STAGE)
    build_runtime(os.path.join(STAGE, "runtime"))
    sha = build_app(os.path.join(STAGE, "app"))
    build_launchers(STAGE)
    return sha


def install_into(target):
    import updater
    data = os.path.join(target, "data")
    try:
        with open(os.path.join(data, "app.pid"), encoding="utf-8") as f:
            if updater._alive(int(f.read().strip() or 0)):
                fail("Budget Tool is open. Close it first, then build again.")
    except (OSError, ValueError):
        pass
    os.makedirs(target, exist_ok=True)
    for old in ("_internal", "app", "runtime", "app.previous", ".update"):  # _internal: the older single-exe build
        shutil.rmtree(os.path.join(target, old), ignore_errors=True)
    shutil.copytree(os.path.join(STAGE, "runtime"), os.path.join(target, "runtime"))
    shutil.copytree(os.path.join(STAGE, "app"), os.path.join(target, "app"))
    for name in LAUNCHERS:
        shutil.copy2(os.path.join(STAGE, name), os.path.join(target, name))


def main():
    args = sys.argv[1:]
    sha = stage()
    say(f"Built Budget Tool {open(os.path.join(HERE, 'VERSION')).read().strip()} ({(sha or 'no commit')[:7]}) in {STAGE}")
    if "--stage-only" in args:
        return
    target = next((a for a in args if not a.startswith("--")), None) or os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Programs", "BudgetTool")
    install_into(target)
    shutil.rmtree(os.path.join(HERE, "dist"), ignore_errors=True)
    say(f"Installed into {target} (your data folder there was left alone).")


if __name__ == "__main__":
    main()
