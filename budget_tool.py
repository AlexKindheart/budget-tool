"""Budget Tool desktop app: opens the app in its own window (no browser needed).

Run from source:  py budget_tool.py      Build the .exe:  build.bat
"""
import sys

import webview

import app as budget_app


def main():
    start_url = budget_app.start_server()
    webview.create_window(
        "Budget Tool", start_url,
        width=1200, height=820, min_size=(760, 560),
        text_select=True,
    )
    webview.start(private_mode=True)  # closing the window quits the app
    sys.exit(0)


if __name__ == "__main__":
    main()
