"""Where the app's files live, both when run from source and as a built .exe."""
import os
import sys

# Read-only app files (templates, static). PyInstaller unpacks these to sys._MEIPASS.
RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

# Your data (database with bank tokens). Kept in %APPDATA%\BudgetTool so it survives app updates.
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "BudgetTool")
os.makedirs(DATA_DIR, exist_ok=True)
