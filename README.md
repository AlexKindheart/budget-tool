# Budget Tool

A personal budgeting app for Windows that works like a money-manager spreadsheet (account register,
yearly budget, budget-vs-actual reports), except your bank transactions fill themselves in through
[Plaid](https://plaid.com). It runs in its own window, and your data stays on your computer.

## Screens

| Screen | What it does |
|---|---|
| **Overview** | This month's income/expenses/net vs. budget, a balances chart (its *All accounts* line is your net worth), spending by category, **Coming up** bills and **Safe to spend**, account balances, recent activity |
| **Register** | Every transaction: Account, Date, Payee, Memo, Category, Payment, Deposit, running Balance. Add cash/manual entries, edit, filter, search, tick several to categorize them at once, split one across categories |
| **Budget** | A yearly grid: each category × 12 months, with Total, Average, and Total Income / Expenses / NET / Projected End Balance |
| **Reports** | Monthly or year-to-date budget vs. actual, a yearly month-by-month report, a weekly / bi-weekly report, and spending **by payee** (with subscriptions and bills that repeat marked) |
| **Accounts** | Accounts with savings goals and % reached, posted and total balances, what the bank says, bank connections, and **Balance it** to check a hand-kept account against a statement |
| **Bills** | Bills and paychecks that repeat (from the Overview's Coming up): what's due, and how much is safe to spend before your next paycheck |
| **Categories** | Income, expense and non-budget categories ([Transfer], [Beginning Balance]). **Plan changes for next year** renames, retires or adds categories from January 1 without changing earlier years; from December 1 the Overview asks if you'd like to. |
| **Export** | Saves an Excel workbook for any date range and accounts: budget vs. actual, every transaction (sortable table), each category by month, and account balances |
| **Settings** | Plaid keys, app updates, your password and automatic lock, backups |

How the numbers work:

- **Expense** categories count as spending (refunds reduce it). **Income** categories count as money in.
- **Non-budget** categories ([Transfer], [Beginning Balance]) are left out of every total. Use them for money moving
  between your own accounts.
- Bank charges that haven't gone through yet show a **pending** label until your bank posts them.
- **Something you typed in that your bank also sends only counts once.** In an account linked to your bank, a bank
  transaction for exactly the same amount, from 3 days before to 14 days after your entry (checks can take a while
  to clear), is combined with it: it keeps your payee, memo and category and shows a **combined** label. **Not the
  same** splits them back apart for good.
- Bank-linked accounts get an automatic [Beginning Balance] entry, so the register's posted balance equals what the
  bank reports.
- **You categorize every transaction yourself.** Bank transactions arrive Uncategorized (highlighted in the Register),
  and a sync never changes a category you've picked. A payee you rename stays renamed.
- Budgets for part of a month (a week, two weeks, a custom export range) are that month's budget spread evenly
  over its days, so the Overview, Reports and Export always agree for the same dates.
- **Split transactions:** one payment for several things (groceries and household supplies on one receipt) can be
  split on its Edit screen. Each part has its own category and amount; the parts must add up to the transaction.
  Reports, the Overview and Export count each part in its own category.
- **Carry over:** tick it for a savings-style expense category (Categories screen). Budget you don't spend moves into
  the next month, so $100 a month with nothing spent becomes $200 available, and overspending takes it out of next
  month. Each year starts fresh in January. Monthly reports show how much was carried.
- **Safe to spend** = what's in your checking and cash accounts, minus the bills due before your next paycheck (or in
  the next 30 days if you haven't added a paycheck). A bill moves on to its next date by itself once a payment of the
  same amount shows up in its account near the due date, or a few days after it.
- **Balance it** (Accounts, for accounts not linked to a bank): enter a statement's ending balance, tick what's on
  it, and when the difference is $0.00 those transactions are marked reconciled.

**Less to do yourself:** the Overview's *Get started* checklist walks a new user through setup, and its *To do* card
lists what needs you (transactions without a category, a category over budget or on pace to go over, a bank to
reconnect, an account that doesn't match the bank, a month with no budget, an old unencrypted copy of your budget
left by an earlier installed version), each one click away. In the Register, a transaction from a payee you've categorized before
shows a one-click suggestion (nothing is categorized for you). When your bank marks a transaction as a credit card
payment you get a **Debt?** suggestion, and as a transfer, **[Transfer]?**. On the Budget screen, **Fill from my spending** fills
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
Unless it's code-signed, Windows SmartScreen shows "Windows protected your PC" the first time: click **More info →
Run anyway**. To sign it, get a code-signing certificate (Azure Trusted Signing, SignPath for open source, or one
from a certificate authority), copy `signing.example.bat` to `signing.bat` and fill in the signing command.
`build-installer.bat` then signs the two launchers and the installer. `signing.bat` and `.pfx` files are never
committed.

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
**Go back to the previous version** link restores it (except to a version from before the password lock, which
couldn't open your encrypted budget). Your `data` folder is never touched. Each update is logged to
`update.log` in the `data` folder.

**Updates must be signed.** The updater only installs a commit signed with the developer's SSH signing key (the
public half is in `TRUSTED_SIGNERS` in `updater.py`; it checks the signature itself rather than trusting GitHub's
"Verified" badge). So someone who got into the GitHub account still couldn't push an update everyone installs, and
an unsigned commit (including one made in GitHub's web editor) is never installed. This repository signs commits with
`~/.ssh/budgettool_signing` (`git config gpg.format ssh`, `user.signingkey`, `commit.gpgsign true`). Keep a copy of
that private key somewhere safe and offline: without it, installed copies can't be updated (only reinstalled).

Because every push reaches every install the next time it updates, only push to `main` what you're happy for
everyone using Budget Tool to run. Changing the bundled Python itself or the launchers needs a fresh `build.bat` /
installer (rare).

## Your data

Everything is stored in `data\budget.db` next to `BudgetTool.exe` (Settings shows where), including your Plaid keys
and bank access tokens. Rebuilding or updating the app never touches the `data` folder.

**Password protection (recommended).** The first time you open Budget Tool (or the first time after updating to
this version) it asks you to choose a password. You can choose **Continue without a password** instead, after a warning:
without one, anyone who uses the computer or gets a copy of the folder can open your budget and see your bank
information. Budget Tool then opens straight away, never locks, and **Settings → Password and lock** shows the same
warning with a place to add a password any time (and, with one, to remove it again). With a password:

- Budget Tool opens locked and reads nothing until you enter your password. It locks again when you close it, when
  you click **Lock**, and after 15 minutes without use (change that in **Settings → Password and lock**).
- `budget.db` is encrypted with SQLCipher (AES-256, with a check on every page that it hasn't been tampered with).
  A copy of the folder or the USB drive is unreadable without your password.
- Your password isn't stored anywhere, not even as a hash. It's stretched with scrypt and then PBKDF2-SHA512
  (600,000 rounds) into the key that unlocks the encryption key, so each guess at it is slow. Repeated wrong guesses
  are slowed down further.
- When you set the password you get a **recovery code**. It's the only way back in if you forget your password:
  nobody, including whoever gave you Budget Tool, can reset it. Write it down and keep it away from the computer.
  **Settings** can change your password or make a new code.
- Upgrading encrypts your budget and the older unencrypted copies Budget Tool made during earlier upgrades, then
  deletes the unencrypted files. If you copied Budget Tool's data from an older installed version, that original in
  `%APPDATA%\BudgetTool` is left as it was: delete it yourself once you're happy.
- Spreadsheets you export are not encrypted. Keep them somewhere private.
- Without a password the budget file uses the same encrypted format, but the key is stored right next to it, so
  it isn't protected. Adding a password later protects it (and your backups) without converting anything.

**Backups:** the first time you open Budget Tool each day it saves an encrypted backup in `data\backups` and keeps the
last 30. **Settings → Backups** has **Back up now** (kept until you delete it), opens the folder, and restores a backup
(your budget as it was just before is backed up first). Backups open with your password, so the backups folder is
safe to copy to a USB drive or cloud storage; copy the whole folder, since it includes `lock.json`, which is what
your password unlocks. To back up everything, copy the whole Budget Tool folder (with Budget Tool closed).

Upgrading from the first version keeps your bank connection and settings, saves a backup of the old database, and
downloads your bank transactions again in the new register format.

The app runs a small server that only this computer can reach, and only with a key that changes each time it starts.

## Files

| File | What it is |
|---|---|
| `budget_tool.py` | Starts the app window |
| `update_gui.py` | The updater window (`Update Budget Tool.exe`) |
| `app.py` | Screens and actions |
| `reports.py` | Budget vs. actual, yearly, weekly, by payee, carry-over and over-budget numbers |
| `bills.py` | Bills and paychecks that repeat; Coming up and Safe to spend |
| `updater.py` | Checks GitHub for newer signed commits and installs them |
| `sigcheck.py` | Checks an update's signature (Ed25519 SSH signatures, plain Python) |
| `plaid_client.py` | Plaid API calls and the sync that fills the register |
| `security.py` | Password, recovery code, and encrypted backups |
| `duplicates.py` | Combines a bank transaction with the same one you typed in yourself |
| `db.py` | Encrypted database and starter categories |
| `paths.py` | Where app files and your data live |
| `templates/`, `static/` | Screen layouts, styles, scripts |
| `build.py`, `build.bat` | Builds the portable folder (private Python, code, launchers), installs it, makes shortcuts |
| `launcher/Launcher.cs` | The two small launchers (`BudgetTool.exe`, `Update Budget Tool.exe`) |
| `build-installer.bat`, `installer.iss` | Builds `BudgetTool-Setup.exe` to send to others (`signing.example.bat`: code signing) |
| `tests/` | Tests on made-up data: `py tests\run_tests.py` (needs `py -m pip install -r requirements.txt`) |
| `VERSION` | The app's version number |

## Testing changes

Run `py tests\run_tests.py` before pushing (install the libraries once with `py -m pip install -r requirements.txt`).
Each test builds a made-up budget in a temporary folder (`tests\fake_data.py`) and points Budget Tool at it, so your
real data is never read or changed. `py tests\run_tests.py lock` runs only the tests with "lock" in their name.
