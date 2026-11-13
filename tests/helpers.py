"""Shared setup for the tests. Every test works on a made-up budget in its own temporary folder:

- BUDGETTOOL_DATA points Budget Tool at that folder, never at your real data folder;
- the Windows registry lookup for an older installed version is blocked, so nothing outside the folder is read;
- the folder is deleted afterwards.
"""
import atexit
import os
import shutil
import sys
import tempfile

TESTS = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(TESTS)
_ROOT = tempfile.mkdtemp(prefix="budgettool-test-")
atexit.register(shutil.rmtree, _ROOT, True)


def block_registry():
    try:
        import winreg
        winreg.OpenKey = lambda *a, **k: (_ for _ in ()).throw(OSError("blocked by the tests"))
    except ImportError:
        pass


def paths_for_test():
    """(project folder, a fresh data folder to use, a made-up unencrypted budget to copy into it)."""
    work = os.path.join(_ROOT, "data")
    # First of all, before anything imports Budget Tool's paths.py (which decides where the data folder is).
    os.environ["BUDGETTOOL_DATA"] = work
    block_registry()
    sys.path.insert(0, PROJECT)
    import paths
    if os.path.normcase(os.path.abspath(paths.DATA_DIR)) != os.path.normcase(os.path.abspath(work)):
        raise SystemExit(f"Refusing to run: Budget Tool's data folder is {paths.DATA_DIR}, not the test folder.")
    import fake_data
    src = fake_data.make(os.path.join(_ROOT, "fake-budget.db"))
    return PROJECT, work, src
