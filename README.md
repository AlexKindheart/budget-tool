# Budget Tool

A personal budgeting app for Windows that works like a money-manager spreadsheet (account register,
yearly budget, budget-vs-actual reports), except your bank transactions fill themselves in through
[Plaid](https://plaid.com). It runs in its own window, and your data stays on your computer.

## Screens

| Screen | What it does |
|---|---|
| **Overview** | This month's income/expenses/net vs. budget, a 6/12/24-month income vs. expenses trend chart, spending by category, account balances, recent activity |
| **Register** | Every transaction: Account, Date, Payee, Memo, Category, Payment, Deposit, running Balance. Add cash/manual entries, edit, filter, search |
| **Budget** | A yearly grid: each category × 12 months, with Total, Average, and Total Income / Expenses / NET / Projected End Balance |
| **Reports** | Monthly or year-to-date budget vs. actual, a yearly month-by-month report, and a weekly / bi-weekly report |
| **Accounts** | Accounts with savings goals and % reached, posted and total balances, what the bank says, and bank connections |
| **Categories** | Income, expense and non-budget categories ([Transfer], [Beginning Balance]) |
| **Export** | Saves an Excel workbook for any date range and accounts: budget vs. actual, every transaction (sortable table), each category by month, and account balances |
| **Settings** | Plaid keys, auto-sync interval, and app updates |

How the numbers work:

- **Expense** categories count as spending (refunds reduce it). **Income** categories count as money in.
- **Non-budget** categories ([Transfer], [Beginning Balance]) are left out of every total. Use them for money moving
  between your own accounts.
- Bank charges that haven't gone through yet show a **pending** label until your bank posts them.
- Bank-linked accounts get an automatic [Beginning Balance] entry, so the register's posted balance equals what the
  bank reports.
- **You categorize every transaction yourself.** Bank transactions arrive Uncategorized (highlighted in the Register),
  and a sync never changes a category you've picked. A payee you rename stays renamed.
- Budgets for part of a month (a week, two weeks, a custom export range) are that month's budget spread evenly
  over its days, so the Overview, Reports and Export always agree for the same dates.

## Install

1. Double-click **`build.bat`**. It builds the app, installs it to `%LOCALAPPDATA%\Programs\BudgetTool`, and adds a
   **Budget Tool** shortcut to your Desktop and Start Menu. Close Budget Tool first if it's open. Run it again
   whenever you change the code.
2. Open Budget Tool from the shortcut.

(Developers can also run it straight from source with `py budget_tool.py`.)

### Installer for someone else

Double-click **`build-installer.bat`** to make `release\BudgetTool-Setup.exe`, a normal Windows installer you can send
to a friend (needs [Inno Setup 6](https://jrsoftware.org/isinfo.php): `winget install JRSoftware.InnoSetup`). It
installs for that user without admin rights, lets them choose the app folder and the data folder (the data folder
is saved in `HKCU\Software\BudgetTool\DataDir`; if they already have data it offers to copy it there), adds Start Menu and
Desktop shortcuts, and appears in **Settings → Apps**
so it can be uninstalled. Uninstalling leaves their data where it is. Every GitHub release also includes
`BudgetTool-Setup.exe`. The installer isn't code-signed, so Windows SmartScreen shows "Windows protected your PC"
the first time: click **More info → Run anyway**.

## Connect your bank

1. Sign up at <https://dashboard.plaid.com/signup> and go to **Developers → Keys**.
2. In Budget Tool, open **Settings** and paste your client ID and secret. Choose **Sandbox** for fake test banks
   (login `user_good` / `pass_good`) or **Production** for your real bank (Plaid must approve production access).
3. On **Accounts**, click **Connect a bank**. Plaid's sign-in opens in your web browser, because banks only allow
   their secure login there. When it says you're done, close the tab and the app refreshes.

Already tracking an account by hand? After connecting its bank, use **Same as … Link** on the Accounts screen. The
bank's transactions then continue that account's register from the day after your last entry, instead of
creating a duplicate account.

If a sync shows `ITEM_LOGIN_REQUIRED` (for example after changing your bank password), click **Reconnect**.

## Updates

Budget Tool checks this repo's GitHub releases every few hours while it's open. When there's a newer version you'll
see a banner. Click **Update now** (or **Settings → Updates**) and it downloads the new version, checks it against
the release's published SHA-256 checksum, closes, swaps in the new program files and reopens. Your data isn't
touched. If copying fails, the previous version is put back. Each update is logged to
`update.log` in the data folder.

### Publishing a new version

1. Change the number in `VERSION` (e.g. `1.1.0`), commit, and push.
2. Double-click **`release.bat`**. It tags the commit `v1.1.0` and pushes the tag.
3. GitHub Actions (`.github/workflows/release.yml`) builds `BudgetTool-windows.zip` plus its `.sha256` and publishes
   the release. The build fails if `VERSION` doesn't match the tag.

## Your data

Everything is stored in `budget.db` in your data folder (`%APPDATA%\BudgetTool` unless you picked another one
in the installer; Settings shows where), including your Plaid keys and bank access tokens. Keep that
folder private. Rebuilding or updating the app doesn't touch it.

Upgrading from the first version keeps your bank connection and settings, saves a copy of the old database next to
it (`budget.db.v1-backup-…`), and downloads your bank transactions again in the new register format.

The app runs a small server that only this computer can reach, and only with a key that changes each time it starts.

## Files

| File | What it is |
|---|---|
| `budget_tool.py` | Starts the app window |
| `app.py` | Screens and actions |
| `reports.py` | Budget vs. actual, yearly, weekly, and trend numbers |
| `updater.py` | Checks GitHub releases, verifies and installs updates |
| `plaid_client.py` | Plaid API calls and the sync that fills the register |
| `db.py` | Database and starter categories |
| `paths.py` | Where app files and your data live |
| `templates/`, `static/` | Screen layouts, styles, scripts |
| `build.bat` | Builds the .exe, installs it, makes shortcuts |
| `build-installer.bat`, `installer.iss` | Builds `BudgetTool-Setup.exe` to send to others |
| `release.bat`, `.github/workflows/release.yml` | Publish a version as a GitHub release |
| `VERSION` | The app's version number |
