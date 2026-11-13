"""The updater's signature check (sigcheck.py, updater.signed_by_developer). Uses a throwaway test key."""
import copy
import os
import sys

import helpers
proj, work, src = helpers.paths_for_test()
FAILS = []


def ok(c, label):
    print(("PASS " if c else "FAIL ") + label)
    if not c:
        FAILS.append(label)


import binascii  # noqa: E402
import sigcheck  # noqa: E402
import updater  # noqa: E402

# RFC 8032, section 7.1, test 1
pk = binascii.unhexlify("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
sg = binascii.unhexlify("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
ok(sigcheck.ed25519_verify(pk, b"", sg), "Ed25519: RFC 8032 test vector")
ok(not sigcheck.ed25519_verify(pk, b"x", sg), "Ed25519: changed message fails")

KEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIP2uMZ7VX47tmHKHsNf8wkmA/LMauH/RLwwBgWHx/iSw test key'
PAYLOAD = 'tree 0123456789012345678901234567890123456789\nauthor T <t@example.com> 0 +0000\ncommitter T <t@example.com> 0 +0000\n\nexample update\n'
SIG = '-----BEGIN SSH SIGNATURE-----\nU1NIU0lHAAAAAQAAADMAAAALc3NoLWVkMjU1MTkAAAAg/a4xntVfju2Ycoew1/zCSYD8sx\nq4f9EvDAGBYfH+JLAAAAADZ2l0AAAAAAAAAAZzaGE1MTIAAABTAAAAC3NzaC1lZDI1NTE5\nAAAAQMA7zEYpWJLIWAX4DqpR9kh0EP/o3Bu6NcLXHKpiLnhGVl9tylmeSEgUm5cIlcNqou\nkFF14xzTngUpPxq0rh/gA=\n-----END SSH SIGNATURE-----\n'
ok(sigcheck.verify_sshsig(SIG, PAYLOAD.encode(), [KEY]), "SSH signature (ssh-keygen -Y sign -n git) verifies")
ok(not sigcheck.verify_sshsig(SIG, PAYLOAD.encode() + b"!", [KEY]), "changed contents fail")
ok(not sigcheck.verify_sshsig(SIG, PAYLOAD.encode(), updater.TRUSTED_SIGNERS), "a key that isn't trusted fails")
ok(not sigcheck.verify_sshsig(SIG, PAYLOAD.encode(), [KEY], namespace="file"), "a signature made for something else fails")
ok(not sigcheck.verify_sshsig("garbage", PAYLOAD.encode(), [KEY]), "garbage fails")

updater.TRUSTED_SIGNERS = [KEY]
commit = {"sha": "a" * 40, "commit": {"tree": {"sha": "0123456789012345678901234567890123456789"},
                                     "verification": {"verified": True, "payload": PAYLOAD, "signature": SIG}}}
ok(updater.signed_by_developer(commit), "updater: a commit signed by a trusted key is accepted")
other = copy.deepcopy(commit); other["commit"]["tree"]["sha"] = "f" * 40
ok(not updater.signed_by_developer(other), "updater: refused when the signed contents name different files")
gh_only = copy.deepcopy(commit); gh_only["commit"]["verification"] = {"verified": True, "payload": None, "signature": None}
ok(not updater.signed_by_developer(gh_only), "updater: GitHub's own 'verified' isn't enough")


class Answer:
    def __init__(self, data):
        self.data, self.ok = data, True

    def json(self):
        return self.data

    def raise_for_status(self):
        pass


updater.requests.get = lambda url, **k: Answer(gh_only)
try:
    updater.verify_commit("a" * 40)
    ok(False, "updater: installing an unsigned commit is refused")
except RuntimeError:
    ok(True, "updater: installing an unsigned commit is refused")
updater.requests.get = lambda url, **k: Answer(commit)
ok(updater.verify_commit("a" * 40)["sha"] == "a" * 40, "updater: installing a signed commit goes ahead")
print("\n" + ("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
