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
| **Categories** | Income, expense and non-budget categories ([Transfer], [Beginning Balance]). **Plan changes for next year** renames, retires or adds categories from January 1 without changing earlier years; from December 1 the Overview asks if you'd like to. |
| **Export** | Saves an Excel workbook for any date range and accounts: budget vs. actual, every transaction (sortable table), each category by month, and account balances |
| **Settings** | Plaid keys and app updates |

How the numbers work:

- **Expense** categories count as spending (refunds reduce it). **Income** categories count as money in.
- **Non-budget** categories ([Transfer], [Beginning Balance]) are left out of every total. Use them for money moving
  between your own accounts. The Overview and Reports show them on a separate *Transfers (not counted)* line.
- **Transfers between your accounts are matched for you.** A payment in one account and a deposit of the same amount
  in another, within 5 days, are paired and both get [Transfer] (the Register shows "↔ Savings" on each side;
  **Not a transfer** undoes it for good). Only Uncategorized transactions are matched, and pending ones wait until
  they post. When your bank marks something as a transfer but the other side isn't in Budget Tool, you get a
  one-click **[Transfer]?** suggestion instead.
- **Credit card and loan payments count as debt paid.** Paying a credit card or loan account from checking (or any
  other everyday account) is matched the same way, but the payment gets your **Debt** category, so it shows as
  spending, and only the card or loan side gets [Transfer]. A card payment whose card isn't in Budget Tool gets a
  one-click **Debt?** suggestion. (The card's purchases count as spending too; pick [Transfer] for the payment if
  you'd rather not count both.)
- **Transfers to check** (on the To do card, and a Register filter) lists a [Transfer] with no other side and a
  transfer-looking transaction that's still Uncategorized. **Other side isn't in Budget Tool** marks a one-sided
  transfer (to a friend, or an account you don't track here) as fine.
- Bank charges that haven't gone through yet show a **pending** label until your bank posts them.
- Bank-linked accounts get an automatic [Beginning Balance] entry, so the register's posted balance equals what the
  bank reports.
- **You categorize every transaction yourself.** Bank transactions arrive Uncategorized (highlighted in the Register),
  and a sync never changes a category you've picked. A payee you rename stays renamed.
- Budgets for part of a month (a week, two weeks, a custom export range) are that month's budget spread evenly
  over its days, so the Overview, Reports and Export always agree for the same dates.

**Less to do yourself:** the Overview's *Get started* checklist walks a new user through setup, and its *To do* card
lists what needs you (transactions without a category, a bank to reconnect, an account that doesn't match the bank,
a month with no budget), each one click away. In the Register, a transaction from a payee you've categorized before
shows a one-click suggestion (nothing is categorized for you). On the Budget screen, **Fill from my spending** fills
empty months with your average over the last 12 months.

## Install

Budget Tool is **portable**: the program and your data live together in one folder, so you can copy that folder to
a USB drive and run it on any Windows 10/11 PC. Nothing is written to the registry or AppData.

```
BudgetTool\
  BudgetTool.exe            starts Budget Tool
  Update Budget Tool.exe    the updater
  runtime\                  a private copy of Python and the libraries Budget Tool uses
  app\                      Budget Tool's code (what updates replace)
  data\                     your budget, settings, bank connections (never touched by updates)
```

1. Double-click **`build.bat`** (needs Python 3.11 on this PC; the folder it makes doesn't). It builds the app into `%LOCALAPPDATA%\Programs\BudgetTool` (or run
   `build.bat E:\BudgetTool` to build straight onto a USB drive) and adds Desktop and Start Menu shortcuts on this PC.
   Close Budget Tool first if it's open. Run it again whenever you change the code; your `data` folder is kept.
2. Open Budget Tool from the shortcut, or double-click `BudgetTool.exe` in the folder.

The first time, if an older installed version left data in `%APPDATA%\BudgetTool`, Budget Tool asks whether to copy
it into the `data` folder (the original is left as it is).

Avoid putting the folder in Program Files: Budget Tool needs to save your data next to itself.

(Developers can also run it straight from source with `py budget_tool.py`.)

### Installer for someone else

Double-click **`build-installer.bat`** to make `release\BudgetTool-Setup.exe`, an installer you can send to a friend
(needs [Inno Setup 6](https://jrsoftware.org/isinfo.php): `winget install JRSoftware.InnoSetup`). It puts the portable
folder wherever they choose (a USB drive works), without admin rights, with an optional Desktop shortcut. There's
no uninstaller and nothing in the registry: to remove Budget Tool, delete the folder. Installing again into the same
folder replaces the program and keeps the data. After that, updates come through **Update Budget Tool** (below).
The installer isn't code-signed, so Windows SmartScreen shows "Windows protected your PC"
the first time: click **More info → Run anyway**.

## Connect your bank

1. Sign up at <https://dashboard.plaid.com/signup> and go to **Developers → Keys**.
2. In Budget Tool, open **Settings** and paste your client ID and secret. Choose **Sandbox** for fake test banks
   (login `user_good` / `pass_good`) or **Production** for your real bank (Plaid must approve production access).
3. On **Accounts**, click **Connect a bank**. Plaid's sign-in opens in your web browser, because banks only allow
   their secure login there. When it says you're done, close the tab and the app refreshes.

After that, Budget Tool syncs with your bank **each time you open it** (it says "Syncing with your bank…" at the top
and refreshes when new transactions arrive). To pick up something new, close Budget Tool and open it again.

Already tracking an account by hand? After connecting its bank, use **Same as … Link** on the Accounts screen. The
bank's transactions then continue that account's register from the day after your last entry, instead of
creating a duplicate account.

If a sync shows `ITEM_LOGIN_REQUIRED` (for example after changing your bank password), click **Reconnect**.

## Updates

**Every commit pushed to `main` on GitHub is an update**; there are no releases to publish. Each install knows the
commit it was built from (`app\COMMIT`).

- Budget Tool checks GitHub every few hours while it's open. When `main` has newer commits you'll see a banner (and
  **Settings → Updates** lists what changed, from the commit messages). **Update now** closes Budget Tool and hands
  over to the updater, which reopens it when it's done.
- Or run **Update Budget Tool** (`Update Budget Tool.exe` in the Budget Tool folder) any time.

The updater downloads exactly that commit's code from GitHub over HTTPS, copies only Budget Tool's own files (code,
templates, styles), installs any new libraries if `requirements.txt` changed, and checks that everything compiles
before swapping the new `app` folder in. The previous version is kept (`app.previous`); the updater's
**Go back to the previous version** link restores it. Your `data` folder is never touched. Each update is logged to
`update.log` in the `data` folder.

Because every push reaches every install the next time it updates, only push to `main` what you're happy for
everyone using Budget Tool to run. Changing the bundled Python itself or the launchers needs a fresh `build.bat` /
installer (rare).

## Your data

Everything is stored in `data\budget.db` next to `BudgetTool.exe` (Settings shows where), including your Plaid keys
and bank access tokens. Keep that folder private, and **treat a USB drive with Budget Tool on it like a wallet**:
anyone with the drive can open your budget. Rebuilding or updating the app never touches the `data` folder.
To back up everything, copy the whole Budget Tool folder (with Budget Tool closed).

Upgrading from the first version keeps your bank connection and settings, saves a copy of the old database next to
it (`budget.db.v1-backup-…`), and downloads your bank transactions again in the new register format.

The app runs a small server that only this computer can reach, and only with a key that changes each time it starts.

## Files

| File | What it is |
|---|---|
| `budget_tool.py` | Starts the app window |
| `update_gui.py` | The updater window (`Update Budget Tool.exe`) |
| `app.py` | Screens and actions |
| `reports.py` | Budget vs. actual, yearly, weekly, and trend numbers |
| `transfers.py` | Matches transfers between your accounts and lists ones to check |
| `updater.py` | Checks GitHub for newer commits and installs them |
| `plaid_client.py` | Plaid API calls and the sync that fills the register |
| `db.py` | Database and starter categories |
| `paths.py` | Where app files and your data live |
| `templates/`, `static/` | Screen layouts, styles, scripts |
| `build.py`, `build.bat` | Builds the portable folder (private Python, code, launchers), installs it, makes shortcuts |
| `launcher/Launcher.cs` | The two small launchers (`BudgetTool.exe`, `Update Budget Tool.exe`) |
| `build-installer.bat`, `installer.iss` | Builds `BudgetTool-Setup.exe` to send to others |
| `VERSION` | The app's version number |
