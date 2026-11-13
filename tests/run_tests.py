"""Runs every test (tests\\test_*.py), each in its own Python process, and says which passed.

    py tests\\run_tests.py              all of them
    py tests\\run_tests.py lock bills   only tests whose file name contains one of these words

Needs the same libraries as Budget Tool:  py -m pip install -r requirements.txt
Every test uses a made-up budget in a temporary folder (see helpers.py); your real data is never touched.
"""
import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main(words):
    try:
        import sqlcipher3  # noqa: F401
        import flask  # noqa: F401
    except ImportError as e:
        print(f"Missing a library ({e.name}). Install them with:  py -m pip install -r requirements.txt")
        return 2
    tests = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
    if words:
        tests = [t for t in tests if any(w in os.path.basename(t) for w in words)]
    failed = []
    for t in tests:
        name = os.path.basename(t)
        start = time.time()
        r = subprocess.run([sys.executable, t], cwd=HERE, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        out = r.stdout + r.stderr
        passes = sum(1 for ln in r.stdout.splitlines() if ln.startswith("PASS "))
        bad = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL ")]
        ok = r.returncode == 0 and "ALL PASSED" in r.stdout and not bad
        print(f"{'ok  ' if ok else 'FAIL'}  {name:28} {passes:3} checks  {time.time() - start:5.1f}s")
        if not ok:
            failed.append(name)
            for ln in bad:
                print("        " + ln)
            if not bad:
                print("        " + "\n        ".join(out.strip().splitlines()[-15:]))
    print()
    print("All tests passed." if not failed else f"{len(failed)} failed: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
