"""Password lock and encrypted backups.

Your budget (data\\budget.db) is encrypted with SQLCipher (AES-256, with a tamper check on every page). The key that
unlocks it is 32 random bytes that is never written down anywhere. What's saved, in data\\lock.json, is that key
"wrapped" twice:

- with your password: scrypt (memory-hard, 128 MB) of the password, then PBKDF2-HMAC-SHA512 (600,000 rounds) of that
  result, each with its own random salt. The wrapped key = key XOR that 32-byte result. A fresh salt is made every
  time the password is set, so the same pad is never used twice.
- with your recovery code (shown once when you set the password), the same way.

Your password itself is never stored, not even as a hash. lock.json also holds a check value (an HMAC made with the
key) so a wrong password is caught straight away. Guessing the password from lock.json costs the same scrypt +
PBKDF2 work per guess as typing it in.

The password is optional. Without one, lock.json holds the key itself ("open"), so Budget Tool opens straight away
and the file format stays the same, but anyone with the folder can read the budget: it's not protected. Adding a
password later wraps the key and removes the open copy; removing it does the reverse.

Changing the password re-wraps the same key, so older backups still open with your new password. Backups
(data\\backups\\) are encrypted copies made with the same key; a copy of lock.json sits next to them so the backups
folder on its own (copied to a USB drive, say) can still be opened with your password or recovery code.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import unicodedata
from datetime import datetime

from paths import DATA_DIR

LOCK_PATH = os.path.join(DATA_DIR, "lock.json")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
KEEP_AUTO_BACKUPS = 30          # one a day when you open Budget Tool: about a month
MIN_PASSWORD = 10
SCRYPT = {"n": 2 ** 17, "r": 8, "p": 1}
PBKDF2_ROUNDS = 600_000
_CHECK_LABEL = b"Budget Tool key check v1"


class WrongPassword(Exception):
    pass


# ---------------------------------------------------------------- key wrapping

def _b64(b):
    return base64.b64encode(b).decode("ascii")


def _unb64(s):
    return base64.b64decode(s.encode("ascii"))


def _stretch(secret, salt, kdf):
    """secret -> scrypt -> PBKDF2-HMAC-SHA512 -> 32 bytes."""
    sc = kdf["scrypt"]
    first = hashlib.scrypt(secret, salt=salt + b"/scrypt", n=sc["n"], r=sc["r"], p=sc["p"],
                           maxmem=512 * 1024 * 1024, dklen=64)
    return hashlib.pbkdf2_hmac("sha512", first, salt + b"/pbkdf2", kdf["pbkdf2_rounds"], dklen=32)


def _kdf():
    return {"scrypt": dict(SCRYPT), "pbkdf2_rounds": PBKDF2_ROUNDS}


def _xor(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def _normalize_password(password):
    return unicodedata.normalize("NFC", password).encode("utf-8")


def _normalize_code(code):
    return "".join(ch for ch in code.upper() if ch.isalnum()).encode("ascii", "ignore")


def _wrap(key, secret):
    salt = secrets.token_bytes(32)
    kdf = _kdf()
    return {"salt": _b64(salt), "kdf": kdf, "wrapped": _b64(_xor(key, _stretch(secret, salt, kdf)))}


def _unwrap(entry, secret):
    return _xor(_unb64(entry["wrapped"]), _stretch(secret, _unb64(entry["salt"]), entry["kdf"]))


def _check(key):
    return _b64(hmac.new(key, _CHECK_LABEL, hashlib.sha256).digest())


def _new_code():
    raw = base64.b32encode(secrets.token_bytes(20)).decode("ascii")  # 160 bits, 32 letters/digits
    return "-".join(raw[i:i + 4] for i in range(0, 32, 4))


def password_problem(password, confirm):
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters. A few unrelated words make a strong password that's easy to remember."
    if password != confirm:
        return "The two passwords don't match."
    if password.strip() != password:
        return "The password can't start or end with a space."
    return None


# ---------------------------------------------------------------- lock.json

def _read(path=LOCK_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(lock):
    for path in (LOCK_PATH, os.path.join(BACKUP_DIR, "lock.json")):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lock, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)


def is_set_up():
    """Is there a password? (If lock.json went missing but the backups folder has a copy, put it back.)"""
    if not os.path.exists(LOCK_PATH):
        spare = os.path.join(BACKUP_DIR, "lock.json")
        if os.path.exists(spare) and not _is_plaintext_db():
            import shutil
            shutil.copyfile(spare, LOCK_PATH)
    return os.path.exists(LOCK_PATH)


def _is_plaintext_db():
    import db
    return db.is_plaintext(db.DB_PATH)


def create(password=None):
    """First time: make the key. With a password, wrap it with the password and a new recovery code and return
    (key, recovery code); without one, store it openly and return (key, None)."""
    key = secrets.token_bytes(32)
    if password is None:
        _write({"version": 1, "open": _b64(key), "check": _check(key)})
        return key, None
    code = _new_code()
    _write({"version": 1, "password": _wrap(key, _normalize_password(password)),
            "recovery": _wrap(key, _normalize_code(code)), "check": _check(key)})
    return key, code


def has_password():
    return "password" in _read()


def open_key():
    """The key, when there's no password (None if there is one)."""
    lock = _read()
    if "open" not in lock:
        return None
    key = _unb64(lock["open"])
    return key if hmac.compare_digest(_check(key), lock["check"]) else None


def add_password(key, password):
    """Protect a budget that had no password. Returns the new recovery code."""
    lock = _read()
    if not hmac.compare_digest(_check(key), lock["check"]):
        raise WrongPassword()
    code = _new_code()
    _write({"version": 1, "password": _wrap(key, _normalize_password(password)),
            "recovery": _wrap(key, _normalize_code(code)), "check": lock["check"]})
    return code


def remove_password(key):
    """Go without a password: the key is stored openly again (the budget is no longer protected)."""
    lock = _read()
    if not hmac.compare_digest(_check(key), lock["check"]):
        raise WrongPassword()
    _write({"version": 1, "open": _b64(key), "check": lock["check"]})


def unlock(password):
    lock = _read()
    if "password" not in lock:
        raise WrongPassword()
    key = _unwrap(lock["password"], _normalize_password(password))
    if not hmac.compare_digest(_check(key), lock["check"]):
        raise WrongPassword()
    return key


def unlock_with_code(code):
    lock = _read()
    if "recovery" not in lock:
        raise WrongPassword()
    key = _unwrap(lock["recovery"], _normalize_code(code))
    if not hmac.compare_digest(_check(key), lock["check"]):
        raise WrongPassword()
    return key


def set_password(key, password):
    lock = _read()
    if not hmac.compare_digest(_check(key), lock["check"]):
        raise WrongPassword()
    lock["password"] = _wrap(key, _normalize_password(password))
    _write(lock)


def new_recovery_code(key):
    """A new recovery code; the old one stops working."""
    lock = _read()
    if not hmac.compare_digest(_check(key), lock["check"]):
        raise WrongPassword()
    code = _new_code()
    lock["recovery"] = _wrap(key, _normalize_code(code))
    _write(lock)
    return code


# ---------------------------------------------------------------- backups

def backups():
    """[{name, when, size, auto}] newest first."""
    out = []
    if os.path.isdir(BACKUP_DIR):
        for name in os.listdir(BACKUP_DIR):
            if name.startswith("budget-") and name.endswith(".db"):
                path = os.path.join(BACKUP_DIR, name)
                st = os.stat(path)
                out.append({"name": name, "when": datetime.fromtimestamp(st.st_mtime), "size": st.st_size,
                            "auto": name.endswith("-auto.db")})
    return sorted(out, key=lambda b: b["name"], reverse=True)


def backup_path(name):
    """A backup's path, only for names backups() lists (nothing outside the backups folder)."""
    if name not in {b["name"] for b in backups()}:
        raise ValueError("That backup doesn't exist.")
    return os.path.join(BACKUP_DIR, name)


def make_backup(kind="auto"):
    """An encrypted copy of the budget in data\\backups. kind: auto (one a day, the newest 30 kept), manual
    (kept until you delete it) or before-restore. Returns the file name."""
    import db
    os.makedirs(BACKUP_DIR, exist_ok=True)
    spare = os.path.join(BACKUP_DIR, "lock.json")
    if os.path.exists(LOCK_PATH) and not os.path.exists(spare):
        _write(_read())
    name = f"budget-{datetime.now():%Y-%m-%d-%H%M%S}-{kind}.db"
    path = os.path.join(BACKUP_DIR, name)
    tmp = path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    with db.connect() as conn:
        db.export_encrypted(conn, tmp)
    db.verify(tmp)
    os.replace(tmp, path)
    if kind == "auto":
        for old in [b for b in backups() if b["auto"]][KEEP_AUTO_BACKUPS:]:
            os.remove(os.path.join(BACKUP_DIR, old["name"]))
    return name


def daily_backup():
    """Once a day (the first time Budget Tool is unlocked that day)."""
    today = f"budget-{datetime.now():%Y-%m-%d}-"
    if not any(b["name"].startswith(today) and b["auto"] for b in backups()):
        return make_backup("auto")
    return None


def restore(name):
    """Put a backup's contents back into your budget. Your budget as it is now is backed up first."""
    import db
    src = backup_path(name)
    db.verify(src)
    before = make_backup("before-restore")
    db.restore_from(src)
    return before
