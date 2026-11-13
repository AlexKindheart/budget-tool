"""Budget Tool desktop app: opens the app in its own window (no browser needed).

Portable: the program and your data (the "data" folder next to it) live in one folder. See paths.py.
Run from source:  py budget_tool.py      Build the .exe:  build.bat
"""
import os
import sqlite3
import sys

# The private Python only searches the folders listed in its settings, so make sure this file's folder (app\) is one.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402


def offer_old_data():
    """First start of the portable version: if an earlier, installed version left data elsewhere on this PC,
    ask before copying it in. The original is never changed or deleted."""
    if os.environ.get("BUDGETTOOL_DATA"):
        return
    target = os.path.join(paths.DATA_DIR, "budget.db")
    asked = os.path.join(paths.DATA_DIR, ".old-data-checked")
    if os.path.exists(target) or os.path.exists(asked):
        return
    import ctypes
    box = ctypes.windll.user32.MessageBoxW
    for old in paths.old_data_dirs():
        answer = box(None, "Found your Budget Tool data from the installed version in:\n\n"
                           f"{old}\n\nCopy it into this folder, so it goes wherever Budget Tool goes?\n\n"
                           f"{paths.DATA_DIR}\n\nYour original stays where it is. Choose No to start fresh here.",
                     "Budget Tool", 0x24)  # Yes / No, question icon
        if answer == 6:  # Yes
            try:
                # SQLite's own backup: a consistent copy even if the old version happens to be open.
                src = sqlite3.connect(f"file:{os.path.join(old, 'budget.db')}?mode=ro", uri=True)
                dst = sqlite3.connect(target)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
                    src.close()
            except sqlite3.Error as e:
                if os.path.exists(target):
                    os.remove(target)
                box(None, f"Couldn't copy your data ({e}). Budget Tool will ask again next time.", "Budget Tool", 0x10)
                sys.exit(1)
        break
    with open(asked, "w", encoding="utf-8") as f:
        f.write("Budget Tool already asked about copying data from an earlier installed version.\n")


def remember_running():
    """data\\app.pid says Budget Tool is open, so the updater waits for it to close before replacing files."""
    pid_path = os.path.join(paths.DATA_DIR, "app.pid")
    with open(pid_path, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    def forget():
        try:
            with open(pid_path, encoding="utf-8") as f:
                if f.read().strip() == str(os.getpid()):
                    os.remove(pid_path)
        except OSError:
            pass
    import atexit
    atexit.register(forget)


def icon_file():
    """Budget Tool's icon, and tell Windows this window is Budget Tool (not Python) so the taskbar shows it."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AlexKindheart.BudgetTool")
    except Exception:
        pass
    path = os.path.join(paths.RESOURCE_DIR, "static", "icon.ico")
    return path if os.path.isfile(path) else None


def main():
    offer_old_data()  # before the app starts (it would create an empty database)
    remember_running()
    import webview
    try:
        import app as budget_app
    except RuntimeError as e:  # e.g. the encryption library didn't install
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, str(e), "Budget Tool", 0x10)
        sys.exit(1)
    start_url = budget_app.start_server()
    webview.create_window(
        "Budget Tool", start_url,
        width=1200, height=820, min_size=(760, 560),
        text_select=True,
    )
    webview.start(private_mode=True, icon=icon_file())  # closing the window quits the app; it keeps nothing between runs
    sys.exit(0)


if __name__ == "__main__":
    main()
