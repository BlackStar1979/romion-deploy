"""romion-deploy release-plane smoke (offline, no docker). Run: python tests/smoke_release.py"""
import base64
import hashlib
import io
import os
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.release import apply_release, rollback, list_releases, ReleaseError  # noqa: E402
from app.compose import effective_dir  # noqa: E402

_p = 0
_f = 0


def check(name, cond):
    global _p, _f
    if cond:
        _p += 1
        print("PASS", name)
    else:
        _f += 1
        print("FAIL", name)


def expect_raises(exc, fn):
    try:
        fn()
    except exc:
        return True
    except Exception as e:
        print("   (wrong exc:", type(e).__name__, e, ")")
        return False
    return False


def make_bundle(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    raw = buf.getvalue()
    return base64.b64encode(raw).decode(), hashlib.sha256(raw).hexdigest(), raw


def traversal_bundle():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        data = b"x"
        ti = tarfile.TarInfo("../evil.txt")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    raw = buf.getvalue()
    return base64.b64encode(raw).decode(), hashlib.sha256(raw).hexdigest()


def main():
    base = tempfile.mkdtemp(prefix="reldeploy_")

    b1, s1, _ = make_bundle({"docker-compose.yml": b"services: {}\n", "Dockerfile": b"FROM scratch\n", "app.txt": b"v1"})
    r1 = apply_release(base, b1, s1)
    check("apply r1 -> current == sha", r1["current"] == s1)
    check("r1 extracted", os.path.isfile(os.path.join(base, "releases", s1, "app.txt")))
    check("effective_dir -> r1 release", effective_dir(base) == os.path.join(base, "releases", s1))
    check("list shows r1 current", list_releases(base)["current"] == s1)

    check("sha mismatch rejected", expect_raises(ReleaseError, lambda: apply_release(base, b1, "deadbeef")))
    check("bad base64 rejected", expect_raises(ReleaseError, lambda: apply_release(base, "!!!not-b64!!!", s1)))
    tb, ts = traversal_bundle()
    check("path traversal rejected", expect_raises(ReleaseError, lambda: apply_release(base, tb, ts)))

    b2, s2, _ = make_bundle({"docker-compose.yml": b"services: {}\n", "Dockerfile": b"FROM scratch\n", "app.txt": b"v2"})
    apply_release(base, b2, s2)
    check("apply r2 -> current flips", list_releases(base)["current"] == s2 and s2 != s1)
    check("effective_dir -> r2 release", effective_dir(base) == os.path.join(base, "releases", s2))

    rb = rollback(base)
    check("rollback -> current back to r1", rb["current"] == s1)
    check("effective_dir follows rollback", effective_dir(base) == os.path.join(base, "releases", s1))

    b3, s3, _ = make_bundle({"docker-compose.yml": b"services: {}\n", "Dockerfile": b"FROM scratch\n", "app.txt": b"v3"})
    res = apply_release(base, b3, s3, keep=1)
    check("prune keep=1 leaves only current", list_releases(base)["releases"] == [s3] and res["current"] == s3)

    import shutil
    shutil.rmtree(base, ignore_errors=True)
    print(f"\nRELEASE SMOKE: {_p} passed, {_f} failed")
    sys.exit(1 if _f else 0)


if __name__ == "__main__":
    main()
