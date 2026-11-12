r"""Where Budget Tool keeps its files. It's portable: the program and your data live together in one folder,

    BudgetTool\
      BudgetTool.exe            starts Budget Tool
      Update Budget Tool.exe    the updater
      runtime\                  a private copy of Python and the libraries Budget Tool uses
      app\                      Budget Tool's code (what updates replace)
      data\                     your budget, settings, bank connections (never touched by updates)

so the whole folder can be copied to a USB drive and run on any Windows PC. Nothing is written to the registry
or to AppData.
"""
import os
import sys

FROZEN = getattr(sys, "frozen", False)

# Where the code is: the app\ folder when installed, or the source folder when run with `py budget_tool.py`.
SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))

# Read-only app files (templates, static) sit next to the code (an older PyInstaller build unpacked them to _MEIPASS).
RESOURCE_DIR = getattr(sys, "_MEIPASS", SOURCE_DIR)

# Installed: code in <folder>\app, with the private Python in <folder>\runtime.
_parent = os.path.dirname(SOURCE_DIR)
INSTALLED = (os.path.basename(SOURCE_DIR).lower() == "app"
             and os.path.isfile(os.path.join(_parent, "runtime", "python.exe")))

# The Budget Tool folder (the one holding BudgetTool.exe), or the source folder when run from source.
APP_DIR = _parent if INSTALLED else (os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else SOURCE_DIR)
RUNTIME_DIR = os.path.join(APP_DIR, "runtime")

# Your data, next to the program. BUDGETTOOL_DATA overrides it (developers / tests).
DATA_DIR = os.environ.get("BUDGETTOOL_DATA") or os.path.join(APP_DIR, "data")

# Where versions before the portable one kept data (only read, to offer copying it in on first start).
OLD_DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "BudgetTool")


def _cant_write(folder, err):
    msg = (f"Budget Tool keeps your data in a \"data\" folder next to the program, but it can't write here:\n\n"
           f"{folder}\n\n({err})\n\n"
           "If this is a USB drive, check it's plugged in, not full, and not write-protected.\n"
           "Programs inside \"Program Files\" can't write next to themselves: move the whole BudgetTool folder "
           "somewhere like Documents, the Desktop or a USB drive, and open it from there.")
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "Budget Tool", 0x10)
    except Exception:
        print(msg, file=sys.stderr)
    sys.exit(1)


try:
    os.makedirs(DATA_DIR, exist_ok=True)
    _probe = os.path.join(DATA_DIR, ".write-test")
    with open(_probe, "w") as _f:
        _f.write("ok")
    os.remove(_probe)
except OSError as _e:
    _cant_write(DATA_DIR, _e.strerror or _e)


def old_data_dirs():
    """Folders where an earlier (installed) version kept its data: the folder picked in the old installer
    (from the registry, read only) and %APPDATA%\\BudgetTool. Only ones that actually have a database."""
    found = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\BudgetTool") as key:
            found.append(os.path.expandvars(str(winreg.QueryValueEx(key, "DataDir")[0])).strip())
    except (ImportError, OSError):
        pass
    found.append(OLD_DATA_DIR)
    here = os.path.normcase(os.path.realpath(DATA_DIR))
    out = []
    for d in found:
        if d and os.path.isfile(os.path.join(d, "budget.db")) and os.path.normcase(os.path.realpath(d)) != here:
            if d not in out:
                out.append(d)
    return out
