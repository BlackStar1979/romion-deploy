"""
romion-deploy - content-addressed release plane (no shell, no arbitrary paths).

Stages an immutable build-context bundle (a tar of source + Dockerfile),
verified by sha256, into <base>/releases/<sha>/ and atomically flips the
<base>/current pointer file to <sha>. `docker compose up -d --build` then runs
inside the active release (see compose.effective_dir). Rollback re-points
`current` to the previous release. Old releases are pruned, keeping the last N.

Security: the bundle is hash-verified before extraction; tar members with
absolute paths, `..` traversal, symlinks/hardlinks/devices are rejected; size
is capped. The caller supplies only an allowlisted stack (resolved to a dir
elsewhere) plus the bundle + its sha256 — never a path.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import shutil
import tarfile
import time
from pathlib import Path
from typing import Any

MAX_BUNDLE_BYTES = 25 * 1024 * 1024
DEFAULT_KEEP = 5


class ReleaseError(Exception):
    pass


def _releases_dir(base: Path) -> Path:
    return base / "releases"


def _pointer(base: Path) -> Path:
    return base / "current"


def _list(base: Path) -> list[str]:
    rd = _releases_dir(base)
    if not rd.is_dir():
        return []
    dirs = [p for p in rd.iterdir() if p.is_dir() and not p.name.startswith(".")]
    return [p.name for p in sorted(dirs, key=lambda p: p.stat().st_mtime)]  # oldest first


def _current_sha(base: Path) -> str | None:
    ptr = _pointer(base)
    if not ptr.is_file():
        return None
    sha = ptr.read_text(encoding="utf-8").strip()
    return sha or None


def _point_current(base: Path, sha: str) -> None:
    ptr = _pointer(base)
    tmp = base / f".current-{int(time.time() * 1000)}.tmp"
    tmp.write_text(sha + "\n", encoding="utf-8")
    os.replace(tmp, ptr)  # atomic pointer swap


def _safe_extract(data: bytes, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
        for m in tf.getmembers():
            name = m.name
            if name.startswith("/") or os.path.isabs(name) or ".." in Path(name).parts:
                raise ReleaseError(f"unsafe path in bundle: {name!r}")
            if m.issym() or m.islnk() or m.isdev() or m.ischr() or m.isblk() or m.isfifo():
                raise ReleaseError(f"unsafe member type in bundle: {name!r}")
        try:
            tf.extractall(dest, filter="data")  # py>=3.12 / security backports
        except TypeError:
            tf.extractall(dest)


def _prune(base: Path, keep: int) -> list[str]:
    keep = max(1, int(keep))
    rels = _list(base)  # oldest first
    cur = _current_sha(base)
    excess = max(0, len(rels) - keep)
    pruned: list[str] = []
    for r in rels:
        if excess <= 0:
            break
        if r == cur:
            continue
        shutil.rmtree(_releases_dir(base) / r, ignore_errors=True)
        pruned.append(r)
        excess -= 1
    return pruned


def apply_release(base_dir: str, bundle_b64: str, sha256_hex: str, keep: int = DEFAULT_KEEP) -> dict[str, Any]:
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    try:
        data = base64.b64decode(bundle_b64, validate=True)
    except Exception as e:
        raise ReleaseError(f"invalid base64 bundle: {e}") from e
    if len(data) > MAX_BUNDLE_BYTES:
        raise ReleaseError(f"bundle too large: {len(data)} > {MAX_BUNDLE_BYTES} bytes")

    digest = hashlib.sha256(data).hexdigest()
    want = str(sha256_hex or "").strip().lower()
    if not want or digest != want:
        raise ReleaseError(f"sha256 mismatch (computed {digest}, expected {want or '(none)'})")

    rel = _releases_dir(base) / digest
    if not rel.is_dir():
        tmp = _releases_dir(base) / f".incoming-{digest[:12]}-{int(time.time() * 1000)}"
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        _safe_extract(data, tmp)
        rel.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, rel)

    _point_current(base, digest)
    pruned = _prune(base, keep)
    return {"status": "ok", "sha256": digest, "current": digest, "pruned": pruned, "releases": _list(base)}


def rollback(base_dir: str) -> dict[str, Any]:
    base = Path(base_dir)
    rels = _list(base)  # oldest..newest
    cur = _current_sha(base)
    previous = [r for r in rels if r != cur]
    if not previous:
        raise ReleaseError("no previous release to roll back to")
    target = previous[-1]  # most recent non-current release
    _point_current(base, target)
    return {"status": "ok", "current": target, "previous_was": cur, "releases": _list(base)}


def list_releases(base_dir: str) -> dict[str, Any]:
    base = Path(base_dir)
    return {"current": _current_sha(base), "releases": _list(base)}
