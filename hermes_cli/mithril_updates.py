"""Signed, side-by-side updates for the Mithril CLI distribution only.

The release owner supplies the public roots in the qualified source tree. No
environment variable or downloaded document can introduce a verification root.
Existing sessions retain their checkout and PM lease when a new CLI is selected.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

REPOSITORY = "mithril-lang/mithril-agent-runtime"
RELEASES = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=10"
CONTROL_ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("MITHRIL_RUNTIME_ROOT", str(CONTROL_ROOT))).resolve()
TRUST_FILE = CONTROL_ROOT / "distributions/mithril/update-trust.json"
SHA = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-preview\.[1-9][0-9]*")
MAX_METADATA = 128 * 1024
MAX_INSTALLER = 1024 * 1024


class UpdateError(RuntimeError):
    """No unverified code or install selection may be published."""


class IncompleteUpdateError(UpdateError):
    """The selection journal requires operator recovery before another chat."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateError("Duplicate release metadata field")
        result[key] = value
    return result


def decode(data):
    if len(data) > MAX_METADATA:
        raise UpdateError("Release metadata is too large")
    try:
        value = json.loads(data, object_pairs_hook=_unique)
    except (ValueError, UnicodeError) as exc:
        raise UpdateError("Invalid release metadata") from exc
    if not isinstance(value, dict):
        raise UpdateError("Release metadata must be an object")
    return value


def load_trust():
    trust = decode(TRUST_FILE.read_bytes())
    if (trust.get("schema") != 1 or trust.get("repository") != REPOSITORY
            or trust.get("channel") != "preview" or not trust.get("keys")):
        raise UpdateError("The Mithril release owner has not provisioned an approved signing root")
    return trust


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        return parsed.timestamp()
    except (AttributeError, TypeError, ValueError) as exc:
        raise UpdateError("Invalid release timestamp") from exc


def verify(envelope, trust, *, now=None, state=None):
    """Authenticate before admitting any artifact URL, timestamp or revision."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        key = trust["keys"][envelope["key_id"]]
        if key.get("revoked") is not False:
            raise UpdateError("Release signer is revoked or not approved")
        public = base64.b64decode(key["public_key"], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        payload = envelope["signed"]
        Ed25519PublicKey.from_public_bytes(public).verify(signature, canonical(payload))
    except (KeyError, TypeError, ValueError, InvalidSignature) as exc:
        raise UpdateError("Mithril release signature could not be verified") from exc
    if not isinstance(payload, dict):
        raise UpdateError("Invalid signed release payload")
    if (payload.get("schema") != 1 or payload.get("product") != "mithril-cli"
            or payload.get("repository") != REPOSITORY or payload.get("channel") != "preview"
            or not isinstance(payload.get("version"), str)
            or not VERSION.fullmatch(payload["version"])
            or not isinstance(payload.get("runtime_revision"), str)
            or not SHA.fullmatch(payload["runtime_revision"])
            or type(payload.get("sequence")) is not int or payload["sequence"] < 1):
        raise UpdateError("Release identity does not match the Mithril preview distribution")
    previous = payload.get("previous_revisions")
    if (not isinstance(previous, list) or not 1 <= len(previous) <= 64
            or any(not isinstance(r, str) or not SHA.fullmatch(r) for r in previous)
            or payload["runtime_revision"] in previous or len(previous) != len(set(previous))):
        raise UpdateError("Release has no explicit compatible upgrade sources")
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    issued, expires = _time(payload.get("issued_at")), _time(payload.get("expires_at"))
    if issued > now + 300 or expires <= now or not 0 < expires - issued <= 31 * 86400:
        raise UpdateError("Release metadata is expired or has an invalid validity period")
    installer = payload.get("installer", {})
    expected = f"https://github.com/{REPOSITORY}/releases/download/v{payload['version']}/install-mithril.sh"
    if (installer.get("url") != expected or type(installer.get("size")) is not int
            or not 0 < installer["size"] <= MAX_INSTALLER
            or not re.fullmatch(r"[0-9a-f]{64}", str(installer.get("sha256", "")))):
        raise UpdateError("Release installer identity is invalid")
    provenance = payload.get("provenance", {})
    if (provenance.get("source_repository") != "mithril-lang/mithril-fund"
            or not SHA.fullmatch(str(provenance.get("source_revision", "")))
            or not re.fullmatch(r"[0-9a-f]{64}", str(provenance.get("manifest_sha256", "")))):
        raise UpdateError("Release source provenance is missing")
    state = state or {}
    highest = state.get("highest_sequence", 0)
    if type(highest) is not int or highest < 0:
        raise UpdateError("Local release history is invalid; preserve it for recovery")
    digest = hashlib.sha256(canonical(payload)).hexdigest()
    if (payload["sequence"] < highest or (payload["sequence"] == highest
            and state.get("manifest_sha256") != digest)):
        raise UpdateError("Release metadata would replay or replace an accepted revision")
    return payload


class _Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.hostname not in
                {"github.com", "api.github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}):
            raise UpdateError("Release download redirected outside the approved artifact origin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, limit):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in {"github.com", "api.github.com"}
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise UpdateError("Unapproved release origin")
    req = Request(url, headers={"User-Agent": "Mithril-CLI-Updater", "Accept": "application/json", "Cache-Control": "no-cache"})
    with build_opener(_Redirects()).open(req, timeout=20) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("Release download exceeds its size limit")
    return data


def latest(trust, state):
    releases = json.loads(download(RELEASES, 1024 * 1024))
    if not isinstance(releases, list):
        raise UpdateError("Release discovery returned no release list")
    for release in releases:
        tag = release.get("tag_name", "")
        if release.get("draft") is not False or release.get("prerelease") is not True or not VERSION.fullmatch(tag.removeprefix("v")):
            continue
        expected = f"https://github.com/{REPOSITORY}/releases/download/{tag}/mithril-update.json"
        assets = [a for a in release.get("assets", []) if a.get("name") == "mithril-update.json"]
        if not assets:
            continue  # Older preview releases predate the signed updater.
        if len(assets) != 1 or assets[0].get("browser_download_url") != expected:
            raise UpdateError("Release metadata asset has an unexpected owner")
        envelope = decode(download(expected, MAX_METADATA))
        target = verify(envelope, trust, state=state)
        if "v" + target["version"] != tag:
            raise UpdateError("Signed release and discovery tag differ")
        return target
    raise UpdateError("No signed Mithril preview release is published")


def _git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE, timeout=20).strip()


def inspect_runtime(root, revision=None):
    root = Path(root).resolve(strict=True)
    if (_git(root, "remote", "get-url", "origin") != f"https://github.com/{REPOSITORY}.git"
            or _git(root, "status", "--porcelain")):
        raise UpdateError("Runtime has another owner or local changes; preserve it before updating")
    actual = _git(root, "rev-parse", "HEAD")
    if not SHA.fullmatch(actual) or (revision is not None and actual != revision):
        raise UpdateError("Prepared runtime revision differs from the signed release")
    if not (root / ".hermes/bin/hermes").is_file():
        raise UpdateError("Prepared runtime has no CLI launcher")
    return {"path": str(root), "revision": actual}


def _write(path, value):
    staged = path.with_name(path.name + "." + uuid.uuid4().hex)
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(staged, path)
    _sync_directory(path.parent)


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _select(pointer, root):
    staged = pointer.with_name(pointer.name + "." + uuid.uuid4().hex)
    staged.symlink_to(root, target_is_directory=True)
    os.replace(staged, pointer)
    _sync_directory(pointer.parent)


@contextmanager
def install_lock(home):
    if os.name != "posix":
        raise UpdateError("This update installer currently supports macOS and Linux")
    import fcntl

    with (home / "mithril-update.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UpdateError("Another Mithril CLI update is in progress") from exc
        yield


def prepare(target, home, store):
    """Use the signed installer in a new HOME; never pass it live Profile data."""
    root = store / target["runtime_revision"]
    if root.exists():
        raise UpdateError("A prepared or interrupted version already exists; preserve it for review")
    data = download(target["installer"]["url"], MAX_INSTALLER)
    if (len(data) != target["installer"]["size"]
            or hashlib.sha256(data).hexdigest() != target["installer"]["sha256"]):
        raise UpdateError("Installer hash or length differs; no code was executed")
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Keep install logs and interrupted preparations for an explicit review.
    temporary = tempfile.mkdtemp(prefix="prepare-", dir=store)
    if temporary:
        isolated = Path(temporary)
        profile = isolated / ".mithril"
        profile.mkdir(mode=0o700)
        # Only PM dependency stores are shared. No config, token, DB or sessions.
        for name in ("tools", "installs"):
            if (home / name).is_dir():
                (profile / name).symlink_to((home / name).resolve(), target_is_directory=True)
        installer = isolated / "install-mithril.sh"
        installer.write_bytes(data)
        env = {k: os.environ[k] for k in ("PATH", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR") if k in os.environ}
        env.update(HOME=str(isolated), HERMES_HOME=str(profile), MITHRIL_AGENT="1")
        subprocess.run(["sh", str(installer), "--home", str(profile), "--dir", str(root),
                        "--bin-dir", str(isolated / "bin")], check=True, env=env, timeout=1800)
        inspect_runtime(root, target["runtime_revision"])
        manifest = (root / ".mithril-runtime-provenance.json").read_bytes()
        if hashlib.sha256(manifest).hexdigest() != target["provenance"]["manifest_sha256"]:
            raise UpdateError("Installed runtime provenance differs from the signed release")
        if json.loads(manifest).get("sourceRevision") != target["provenance"]["source_revision"]:
            raise UpdateError("Installed runtime has another canonical source")
        subprocess.run([str(root / ".hermes/bin/hermes"), "--help"], check=True, env=env,
                       stdout=subprocess.DEVNULL, timeout=90)
    return inspect_runtime(root, target["runtime_revision"])


def apply(target, home, state, *, prepare_runtime=prepare):
    """Journal before switching; interrupted switches require explicit rollback."""
    pointer, receipt = home / "agent-current", home / "mithril-update.json"
    current = inspect_runtime(pointer if pointer.is_symlink() else SOURCE)
    if state.get("pending"):
        raise IncompleteUpdateError("An interrupted update requires mithril update --rollback")
    if current["revision"] == target["runtime_revision"]:
        return None
    if current["revision"] not in target.get("previous_revisions", []):
        raise UpdateError("This release does not qualify an upgrade from the installed revision")
    if pointer.exists() and not pointer.is_symlink():
        raise UpdateError("The CLI selection path is not an updater-owned symlink")
    # Keep source releases on the same Work volume as the current runtime.
    install_id = hashlib.sha256(str(home.resolve()).encode()).hexdigest()[:16]
    store = Path(current["path"]).parent / ".mithril-cli-releases" / install_id
    prepared = prepare_runtime(target, home, store)
    prepared = inspect_runtime(prepared["path"], target["runtime_revision"])
    journal = dict(state, pending=True, previous=current, current=prepared)
    _write(receipt, journal)
    _select(pointer, prepared["path"])
    _write(receipt, dict(journal, pending=False))
    return Path(prepared["path"])


def rollback(home, state):
    previous = state.get("previous")
    if not isinstance(previous, dict):
        raise UpdateError("No previous CLI selection is recorded")
    checked = inspect_runtime(previous["path"], previous["revision"])
    pointer = home / "agent-current"
    if pointer.exists() and not pointer.is_symlink():
        raise UpdateError("The CLI selection path is not an updater-owned symlink")
    _select(pointer, checked["path"])
    # Retain the high-water mark, including on an operator-requested rollback.
    _write(home / "mithril-update.json", dict(state, pending=False, current=checked, previous=None))
    return checked


def run(home, *, check=False, undo=False):
    home = Path(home).resolve(strict=True)
    with install_lock(home):
        receipt = home / "mithril-update.json"
        state = decode(receipt.read_bytes()) if receipt.exists() else {}
        if undo:
            return rollback(home, state)
        if state.get("pending") and not check:
            raise IncompleteUpdateError("An interrupted update requires mithril update --rollback")
        trust = load_trust()
        target = latest(trust, state)
        state.update(highest_sequence=target["sequence"], manifest_sha256=hashlib.sha256(canonical(target)).hexdigest())
        _write(receipt, state)
        if check:
            return target
        selected = apply(target, home, state)
        return {"selected": str(selected)} if selected else {"selected": None}


def before_chat(argv, *, restart_argv=None):
    from hermes_cli.config import load_config_readonly

    config = load_config_readonly().get("updates", {})
    if not isinstance(config, dict) or config.get("check", True) is not True:
        return
    if not os.environ.get("MITHRIL_INSTALL_HOME") or os.environ.get("MITHRIL_UPDATE_REEXEC") == "1":
        return
    try:
        result = run(os.environ["MITHRIL_INSTALL_HOME"], check=config.get("auto_apply", True) is not True)
        if "selected" not in result:
            print(f"Mithril CLI {result['version']} is available. Run mithril update to install it.")
        elif result["selected"]:
            os.environ["MITHRIL_UPDATE_REEXEC"] = "1"
            executable = str(Path(result["selected"]) / ".hermes/bin/hermes")
            os.execv(executable, [executable, *(argv if restart_argv is None else restart_argv)])
    except IncompleteUpdateError as exc:
        print(f"Mithril CLI update needs recovery: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except (UpdateError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Mithril CLI update unavailable: {exc}. Continuing with the installed version.", file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mithril update", description="Verify and install signed Mithril CLI releases.")
    parser.add_argument("command", choices=["update"])
    parser.add_argument("-p", "--profile")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true")
    group.add_argument("--rollback", action="store_true")
    args = parser.parse_args(argv)
    try:
        from hermes_cli.mithril_auth import select_home
        select_home(args.profile)
        from hermes_cli.config import is_managed
        if is_managed():
            raise UpdateError("This Profile is managed; its administrator owns runtime updates")
        home = os.environ.get("MITHRIL_INSTALL_HOME")
        if not home:
            raise UpdateError("Run this command through the Mithril distribution launcher")
        result = run(home, check=args.check, undo=args.rollback)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (UpdateError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Mithril CLI update failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
