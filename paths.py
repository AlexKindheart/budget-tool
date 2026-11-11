"""Where the app's files live, both when run from source and as a built .exe."""
import os
import sys

# Read-only app files (templates, static). PyInstaller unpacks these to sys._MEIPASS.
RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

DEFAULT_DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "BudgetTool")
REGISTRY_KEY = r"Software\BudgetTool"  # HKEY_CURRENT_USER; BudgetTool-Setup.exe writes DataDir here


def _chosen_data_dir():
    """The data folder picked in the installer (saved in the registry), if any."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "DataDir")
        return os.path.expandvars(str(value)).strip() or None
    except (ImportError, OSError):
        return None


def _cant_reach(folder, err):
    """The chosen data folder is missing (e.g. an unplugged drive): say so instead of starting empty."""
    msg = (f"Budget Tool can't open its data folder:\n\n{folder}\n\n({err})\n\n"
           "If it's on a USB or network drive, connect it and open Budget Tool again. "
           "To use a different folder, run BudgetTool-Setup.exe again and pick it there.")
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "Budget Tool", 0x10)
    except Exception:
        print(msg, file=sys.stderr)
    sys.exit(1)


# Your data (database with bank tokens), kept outside the app folder so it survives updates.
# Where: BUDGETTOOL_DATA if set (developers/tests) > the folder chosen in the installer > %APPDATA%\BudgetTool.
DATA_DIR = os.environ.get("BUDGETTOOL_DATA") or _chosen_data_dir() or DEFAULT_DATA_DIR
if DATA_DIR == DEFAULT_DATA_DIR or os.environ.get("BUDGETTOOL_DATA"):
    os.makedirs(DATA_DIR, exist_ok=True)
else:
    # A chosen folder: create it only if the drive / parent folder is there, so an unplugged drive
    # doesn't quietly start you off with an empty budget.
    parent = os.path.dirname(os.path.abspath(DATA_DIR))
    if not os.path.isdir(DATA_DIR):
        if not os.path.isdir(parent):
            _cant_reach(DATA_DIR, "the folder or drive it's on isn't there")
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
        except OSError as e:
            _cant_reach(DATA_DIR, e.strerror or e)
