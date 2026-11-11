# Budget Tool

A personal budgeting app for Windows that pulls transactions from your bank automatically
through [Plaid](https://plaid.com). It runs in its own window, and your data stays on your computer.

- Connect one or more banks/credit cards
- Transactions sync automatically (every 6 hours while the app is open, or click **Sync now**)
- Transactions are categorized automatically. Fix any of them, and tick **always** to make a rule for that merchant
- Set a monthly budget per category and track spent vs. budget on the dashboard

## Install

1. Double-click **`build.bat`**. It builds the app, installs it to
   `%LOCALAPPDATA%\Programs\BudgetTool`, and adds a **Budget Tool** shortcut to your Desktop and Start Menu.
   Close Budget Tool first if it's open. Run it again whenever you change the code.
2. Open Budget Tool from the shortcut.

(Developers can also run it straight from source with `py budget_tool.py`.)

## Connect Plaid

1. Sign up at <https://dashboard.plaid.com/signup>, then go to **Developers → Keys**.
2. In Budget Tool, open **Settings**, paste your **client ID** and **Sandbox secret**, choose
   **Sandbox**, and save.
3. Click **Connect bank**. Plaid's sign-in opens in your web browser. Banks only allow their secure
   login in a real browser. In sandbox, pick any bank and log in with `user_good` / `pass_good`.
   When it says you're done, close the tab. The app window refreshes on its own.

## Using your real bank

Sandbox only has fake banks. To connect your real accounts:

1. In the Plaid Dashboard, request **Production** access (free "limited production" access is available
   for personal projects). Plaid has to approve it, with the **Transactions** product enabled.
2. In **Settings**, paste the **Production** secret and switch the environment to **Production**.
3. Disconnect the sandbox test banks on the **Banks** page, then click **Connect bank**.

Some banks (e.g. Chase, Wells Fargo) only appear in production after you complete Plaid's OAuth
registration and security questionnaire in the Plaid Dashboard.

## How the numbers work

- **Expense** categories count as spending. Refunds show as negative spending.
- **Income** categories count as money in.
- **Transfer** categories (moving money between your own accounts, credit card payments) are left
  out of both, so a card purchase isn't counted again when you pay the card off.
- Change a category's type on the **Budgets** page.

If a sync shows `ITEM_LOGIN_REQUIRED` (for example after changing your bank password), go to
**Banks** and click **Reconnect**.

## Your data

Everything is stored in `%APPDATA%\BudgetTool\budget.db`, including your Plaid keys and bank access
tokens. Keep that folder private. Rebuilding or updating the app doesn't touch it. Delete it to start fresh.

The app runs a small server that only this computer can reach, and only with a key that changes each
time the app starts.

## Files

| File | What it is |
|---|---|
| `budget_tool.py` | Starts the app window |
| `app.py` | Screens and actions |
| `plaid_client.py` | Plaid API calls and transaction sync |
| `db.py` | Database, default categories, Plaid → category mapping |
| `paths.py` | Where app files and your data live |
| `templates/`, `static/` | Screen layouts, styles, and the Connect bank script |
| `build.bat` | Builds the .exe and makes shortcuts |
