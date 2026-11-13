"""Checks that an update was signed by Budget Tool's developer (SSH signatures on git commits, Ed25519).

Plain Python, no libraries: the updater must be able to check a signature before it installs anything.
Ed25519 verification follows RFC 8032 (section 5.1.7); the SSH signature format is OpenSSH's PROTOCOL.sshsig.
"""
import base64
import hashlib
import struct

# ---------------------------------------------------------------- Ed25519 (RFC 8032), verification only

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _inv(x):
    return pow(x, _P - 2, _P)


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _I % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * _inv(5) % _P
_G = (_recover_x(_GY, 0), _GY, 1, _recover_x(_GY, 0) * _GY % _P)


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s, p):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _equal(p, q):
    return (p[0] * q[2] - q[0] * p[2]) % _P == 0 and (p[1] * q[2] - q[1] * p[2]) % _P == 0


def _decompress(b):
    if len(b) != 32:
        return None
    y = int.from_bytes(b, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def ed25519_verify(public, message, signature):
    if len(public) != 32 or len(signature) != 64:
        return False
    a = _decompress(public)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % _L
    return _equal(_mul(s, _G), _add(r, _mul(h, a)))


# ---------------------------------------------------------------- SSH signatures (git commit signing)

def _string(buf, pos):
    (n,) = struct.unpack(">I", buf[pos:pos + 4])
    return buf[pos + 4:pos + 4 + n], pos + 4 + n


def _pack(b):
    return struct.pack(">I", len(b)) + b


def public_key_blob(line):
    """'ssh-ed25519 AAAA... comment' -> the key's wire bytes."""
    parts = line.split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise ValueError("Only ssh-ed25519 keys are supported.")
    return base64.b64decode(parts[1])


def verify_sshsig(armored, message, trusted_keys, namespace="git"):
    """True if `armored` (-----BEGIN SSH SIGNATURE-----) is a valid signature of `message` (bytes) by one of
    `trusted_keys` ('ssh-ed25519 AAAA...' lines), made for `namespace`."""
    try:
        lines = [ln.strip() for ln in armored.strip().splitlines()]
        if lines[0] != "-----BEGIN SSH SIGNATURE-----" or lines[-1] != "-----END SSH SIGNATURE-----":
            return False
        blob = base64.b64decode("".join(lines[1:-1]))
        if blob[:6] != b"SSHSIG":
            return False
        (version,) = struct.unpack(">I", blob[6:10])
        if version != 1:
            return False
        pos = 10
        pub, pos = _string(blob, pos)
        ns, pos = _string(blob, pos)
        reserved, pos = _string(blob, pos)
        hash_alg, pos = _string(blob, pos)
        sig, pos = _string(blob, pos)
        if ns.decode() != namespace or hash_alg not in (b"sha512", b"sha256"):
            return False
        if pub not in {public_key_blob(k) for k in trusted_keys}:
            return False
        key_type, kpos = _string(pub, 0)
        key, _ = _string(pub, kpos)
        sig_type, spos = _string(sig, 0)
        raw_sig, _ = _string(sig, spos)
        if key_type != b"ssh-ed25519" or sig_type != b"ssh-ed25519":
            return False
        digest = hashlib.new(hash_alg.decode(), message).digest()
        signed = b"SSHSIG" + _pack(ns) + _pack(reserved) + _pack(hash_alg) + _pack(digest)
        return ed25519_verify(key, signed, raw_sig)
    except (ValueError, IndexError, struct.error, UnicodeDecodeError):
        return False
