"""Exclusive, restart-safe placement of scheduled profiles on POSIX runtimes.

Transfer is explicit and has no automatic failover. A source remains fenced after
release; only its matching release proof can activate the staged destination.
"""
from __future__ import annotations

import base64
import contextlib
from contextvars import ContextVar
from functools import wraps
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import secrets
import sqlite3
import tarfile
import tempfile
import uuid

STATE = ".execution-placement.json"
LIMIT = 128 * 1024 * 1024
FILES = {"config.yaml", "profile.yaml", "SOUL.md", ".env", "auth.json", "honcho.json",
         "desktop.json", "mithril-token.json", "state.db", "kanban.db", "response_store.db",
         "runs_idempotency.db"}
DIRS = {"cron", "sessions", "memories", "skills", "assets", "workspace", "scripts", "hooks", "plugins"}
_ticket = ContextVar("profile_placement_ticket", default=None)


class PlacementError(RuntimeError):
    pass


def location():
    """A container launcher supplies its stable DO identity outside profile data."""
    marker = Path("/run/mithril-runtime-location")
    if marker.exists():
        value = marker.read_text().strip()
        if not re.fullmatch(r"container:[a-zA-Z0-9_-]{1,128}", value):
            raise PlacementError("Invalid runtime location")
        return value
    return "host:" + platform.node()


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read(home):
    path = Path(home) / STATE
    if not path.exists():
        return None
    try:
        row = json.loads(path.read_text())
        assert row["phase"] in {"active", "draining", "frozen", "staged", "moved"}
        assert re.fullmatch(r"[a-f0-9]{32}", row["identity"])
        assert isinstance(row["generation"], int) and not isinstance(row["generation"], bool)
        assert row["generation"] >= 0 and isinstance(row["owner"], str)
        return row
    except (ValueError, KeyError, AssertionError, TypeError):
        raise PlacementError("Invalid profile placement record") from None


@contextlib.contextmanager
def lock(home, *, exclusive=False, control=False):
    import fcntl
    home = Path(home).resolve()
    root = home.parent / ".placement-locks"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / (home.name + (".control" if control else ".execution"))).open("a+b") as stream:
        try:
            fcntl.flock(stream, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PlacementError("Profile still has admitted work") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _owner(row):
    return row["identity"], row["generation"], row["owner"]


def status(home):
    row = read(home)
    if row is None:
        return {"phase": "unmanaged"}
    return {k: row[k] for k in ("phase", "identity", "generation", "owner", "operation", "target", "sha256") if k in row}


@contextlib.contextmanager
def execution(home):
    home = Path(home).resolve()
    # Legacy Windows profiles are unchanged; enrolled placement is POSIX-only.
    if os.name != "posix" and read(home) is None:
        yield
        return
    with lock(home):
        row = read(home)
        # Hold the lease even before enrollment: otherwise an already-running
        # unregistered job could be invisible to the enrollment/freeze barrier.
        if row is None:
            yield
            return
        ticket = _ticket.get()
        nested = ticket is not None and ticket["live"] and ticket["home"] == home and ticket["owner"] == _owner(row)
        if row["owner"] != location() or (row["phase"] != "active" and not (nested and row["phase"] == "draining")):
            raise PlacementError("Profile execution belongs to another location or is being transferred")
        held = {"home": home, "owner": _owner(row), "live": True}
        token = _ticket.set(held)
        try:
            yield
        finally:
            held["live"] = False
            _ticket.reset(token)


def guard_turn(fn):
    @wraps(fn)
    def call(agent, *args, **kwargs):
        from hermes_constants import get_hermes_home
        home = Path(agent.logs_dir).parent if getattr(agent, "logs_dir", None) else get_hermes_home()
        with execution(home):
            row = read(home)
            owner = _owner(row) if row else None
            if fn.__name__ != "init_agent" and getattr(agent, "_placement_owner", None) != owner:
                raise PlacementError("Cached agent belongs to an earlier placement generation")
            result = fn(agent, *args, **kwargs)
            if fn.__name__ == "init_agent":
                agent._placement_owner = owner
            return result
    return call


def guard_job(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        from hermes_constants import get_hermes_home
        with execution(get_hermes_home()):
            return fn(*args, **kwargs)
    return call


def guard_tick(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        try:
            return guard_job(fn)(*args, **kwargs)
        except PlacementError:
            return 0  # A non-owner never advances, claims or dispatches an occurrence.
    return call


def enroll(home):
    home = Path(home).resolve()
    with lock(home, exclusive=True, control=True), lock(home, exclusive=True):
        row = read(home)
        if row is None:
            if not (home / "profile.yaml").is_file():
                raise PlacementError("An existing named profile is required")
            row = {"phase": "active", "identity": uuid.uuid4().hex, "generation": 0, "owner": location()}
            _write(home / STATE, row)
        if row["owner"] != location() or row["phase"] != "active":
            raise PlacementError("Enrollment cannot reset an existing owner")
        return status(home)


def freeze(home, operation, target):
    home = Path(home).resolve()
    if not re.fullmatch(r"[a-f0-9]{32}", operation) or not isinstance(target, str) or target == location():
        raise PlacementError("A unique operation and different destination are required")
    with lock(home, exclusive=True, control=True):
        row = read(home)
        if not row or row["owner"] != location():
            raise PlacementError("Only the current owner can transfer this profile")
        if row["phase"] == "active":
            row.update(phase="draining", operation=operation, target=target, release=secrets.token_hex(32))
            _write(home / STATE, row)
        if row.get("operation") != operation or row.get("target") != target:
            raise PlacementError("Another transfer owns the profile")
        if row["phase"] == "draining":
            try:
                with lock(home, exclusive=True):
                    row["phase"] = "frozen"
                    _write(home / STATE, row)
            except PlacementError:
                pass  # Keep new admissions closed; retry after the admitted cohort drains.
        return status(home)


def snapshot(home):
    """Include cron and workspace; SQLite online backup captures committed WAL rows."""
    home = Path(home)
    out = io.BytesIO()
    total = 0
    with tempfile.TemporaryDirectory() as scratch, gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as archive:
        for p in sorted(home.rglob("*")):
            name = p.relative_to(home)
            if name.parts[0] not in FILES | DIRS or any(x.endswith(("-wal", "-shm", ".lock")) or x in {".git", "__pycache__"} for x in name.parts):
                continue
            if p.is_symlink():
                raise PlacementError("Profile links require an explicit materialization plan")
            if not p.is_file():
                continue
            source = p
            if p.suffix in {".db", ".sqlite", ".sqlite3"}:
                source = Path(scratch) / uuid.uuid4().hex
                with sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10) as db, sqlite3.connect(source) as copy:
                    db.backup(copy)
                    if copy.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                        raise PlacementError("Profile database failed integrity check")
            total += source.stat().st_size
            if total > LIMIT:
                raise PlacementError("Profile transfer exceeds the size limit")
            info = tarfile.TarInfo(name.as_posix())
            info.size, info.mode = source.stat().st_size, 0o600
            with source.open("rb") as stream:
                archive.addfile(info, stream)
    return out.getvalue()


def export(home, operation, key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    home = Path(home)
    with lock(home, exclusive=True, control=True), lock(home, exclusive=True):
        row = read(home)
        if not row or row["phase"] != "frozen" or row.get("operation") != operation:
            raise PlacementError("Only a fully drained matching transfer can export")
        payload = snapshot(home)
        digest = hashlib.sha256(payload).hexdigest()
        if row.get("sha256", digest) != digest:
            raise PlacementError("Frozen source changed; transfer cannot be retried with different data")
        row["sha256"] = digest
        _write(home / STATE, row)
        capsule = {k: row[k] for k in ("identity", "generation", "operation", "target", "sha256")}
        capsule.update(name=home.name, release_hash=hashlib.sha256(row["release"].encode()).hexdigest())
        nonce = os.urandom(12)
        aad = json.dumps(capsule, sort_keys=True).encode()
        capsule["archive"] = base64.b64encode(nonce + AESGCM(bytes.fromhex(key)).encrypt(nonce, payload, aad)).decode()
        return capsule


def release(home, operation, digest):
    home = Path(home)
    with lock(home, exclusive=True, control=True), lock(home, exclusive=True):
        row = read(home)
        if not row or row["phase"] not in {"frozen", "moved"} or row.get("operation") != operation or row.get("sha256") != digest:
            raise PlacementError("Release does not match the staged transfer")
        row["phase"] = "moved"
        _write(home / STATE, row)
        return row["release"]


def stage(home, capsule, key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    home = Path(home)
    if home.name != capsule["name"] or capsule["target"] != location():
        raise PlacementError("Transfer is addressed to another runtime")
    if any(not re.fullmatch(r"[a-f0-9]{32}", capsule[k]) for k in ("identity", "operation")) or not isinstance(capsule["generation"], int) or isinstance(capsule["generation"], bool) or capsule["generation"] < 0:
        raise PlacementError("Invalid transfer identity or generation")
    data = base64.b64decode(capsule["archive"], validate=True)
    if len(data) > LIMIT * 2:
        raise PlacementError("Transfer exceeds the size limit")
    aad = json.dumps({k: v for k, v in capsule.items() if k != "archive"}, sort_keys=True).encode()
    payload = AESGCM(bytes.fromhex(key)).decrypt(data[:12], data[12:], aad)
    if hashlib.sha256(payload).hexdigest() != capsule["sha256"]:
        raise PlacementError("Transfer digest mismatch")
    with lock(home, exclusive=True, control=True), lock(home, exclusive=True):
        if home.exists():
            row = read(home)
            if row and row.get("operation") == capsule["operation"] and row.get("sha256") == capsule["sha256"] and row["phase"] in {"staged", "active"}:
                return status(home)
            raise PlacementError("Destination already contains profile data")
        with tempfile.TemporaryDirectory(prefix=".placement-", dir=home.parent) as temporary:
            staged = Path(temporary) / "profile"
            staged.mkdir(mode=0o700)
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                entries = archive.getmembers()
                if sum(x.size for x in entries) > LIMIT or any(not x.isfile() or Path(x.name).is_absolute() or ".." in Path(x.name).parts or Path(x.name).parts[0] not in FILES | DIRS for x in entries):
                    raise PlacementError("Unsafe transfer archive")
                for item in entries:
                    p = staged / item.name
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(archive.extractfile(item).read())
                    p.chmod(0o600)
            row = {k: capsule[k] for k in ("identity", "operation", "sha256", "release_hash")}
            row.update(phase="staged", generation=capsule["generation"] + 1, owner=capsule["target"])
            _write(staged / STATE, row)
            staged.rename(home)
        return status(home)


def activate(home, operation, proof):
    home = Path(home)
    with lock(home, exclusive=True, control=True), lock(home, exclusive=True):
        row = read(home)
        if not row or row["owner"] != location() or row["phase"] not in {"staged", "active"} or row.get("operation") != operation or not secrets.compare_digest(row.get("release_hash", ""), hashlib.sha256(proof.encode()).hexdigest()):
            raise PlacementError("Activation requires matching durable source release proof")
        row["phase"] = "active"
        _write(home / STATE, row)
        return status(home)
