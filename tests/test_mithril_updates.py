"""Signed release admission and actual filesystem selection/rollback boundaries."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from hermes_cli import mithril_updates as update


@pytest.fixture
def release():
    key = Ed25519PrivateKey.generate()  # Test-only signer; never a production root.
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    trust = {"keys": {"test": {"public_key": base64.b64encode(public).decode(), "revoked": False}}}
    payload = {"schema": 1, "product": "mithril-cli", "repository": update.REPOSITORY,
               "channel": "preview", "version": "0.1.0-preview.7", "sequence": 7,
               "runtime_revision": "a" * 40, "issued_at": "2026-10-25T00:00:00Z",
               "previous_revisions": ["e" * 40],
               "expires_at": "2026-11-01T00:00:00Z",
               "installer": {"url": f"https://github.com/{update.REPOSITORY}/releases/download/v0.1.0-preview.7/install-mithril.sh",
                             "size": 10, "sha256": "b" * 64},
               "provenance": {"source_repository": "mithril-lang/mithril-fund",
                              "source_revision": "c" * 40, "manifest_sha256": "d" * 64}}
    def sign(value):
        return {"signed": value, "key_id": "test", "signature": base64.b64encode(key.sign(update.canonical(value))).decode()}
    return payload, trust, sign


def test_signature_and_monotonic_release_admission(release):
    payload, trust, sign = release
    now = update._time("2026-10-25T12:00:00Z")
    assert update.verify(sign(payload), trust, now=now) == payload
    state = {"highest_sequence": 7, "manifest_sha256": hashlib.sha256(update.canonical(payload)).hexdigest()}
    assert update.verify(sign(payload), trust, now=now, state=state) == payload
    changed = dict(payload, sequence=6)
    with pytest.raises(update.UpdateError, match="replay"):
        update.verify(sign(changed), trust, now=now, state=state)
    with pytest.raises(update.UpdateError, match="replay"):
        update.verify(sign(dict(payload, runtime_revision="f" * 40)), trust, now=now, state=state)


@pytest.mark.parametrize("case", ["tampered", "unknown-key", "revoked", "expired", "future", "wrong-owner", "wrong-artifact", "missing-provenance"])
def test_untrusted_release_fails_closed(release, case):
    payload, trust, sign = release
    payload, trust = copy.deepcopy(payload), copy.deepcopy(trust)
    if case == "wrong-owner":
        payload["repository"] = "other/runtime"
    if case == "wrong-artifact":
        payload["installer"]["url"] = "https://evil.example/install.sh"
    if case == "missing-provenance":
        payload.pop("provenance")
    if case == "expired":
        payload["expires_at"] = "2026-10-25T01:00:00Z"
    if case == "future":
        payload["issued_at"] = "2026-10-26T00:00:00Z"
    envelope = sign(payload)
    if case == "tampered":
        envelope["signed"]["runtime_revision"] = "f" * 40
    if case == "unknown-key":
        envelope["key_id"] = "unknown"
    if case == "revoked":
        trust["keys"]["test"]["revoked"] = True
    with pytest.raises(update.UpdateError):
        update.verify(envelope, trust, now=update._time("2026-10-25T12:00:00Z"))


def checkout(path, value):
    path.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(path), *args], stderr=subprocess.DEVNULL, text=True).strip()
    git("init")
    git("config", "user.email", "fixture@example.test")
    git("config", "user.name", "Fixture")
    git("remote", "add", "origin", f"https://github.com/{update.REPOSITORY}.git")
    (path / "source").write_text(value)
    git("add", "source")
    git("commit", "-m", value)
    launcher = path / ".hermes/bin/hermes"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\nexit 0\n")
    launcher.chmod(0o755)
    (path / ".git/info/exclude").write_text(".hermes/\n")
    return {"path": str(path), "revision": git("rev-parse", "HEAD")}


def test_real_selection_retains_open_old_runtime_and_profile_then_rolls_back(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    old, new = checkout(tmp_path / "old", "old"), checkout(tmp_path / "new", "new")
    monkeypatch.setattr(update, "SOURCE", Path(old["path"]))
    (home / "state.db").write_bytes(b"retained-conversations")
    (home / ".env").write_text("EXISTING_SECRET=preserved\n")
    state = {"highest_sequence": 7, "manifest_sha256": "accepted"}
    target = {"runtime_revision": new["revision"], "previous_revisions": [old["revision"]]}
    with (Path(old["path"]) / "source").open() as held:
        selected = update.apply(target, home, state, prepare_runtime=lambda *_: new)
        assert selected == Path(new["path"])
        assert (home / "agent-current").resolve() == Path(new["path"])
        assert held.read() == "old"
        journal = json.loads((home / "mithril-update.json").read_text())
        update.rollback(home, journal)
    assert (home / "agent-current").resolve() == Path(old["path"])
    assert (home / "state.db").read_bytes() == b"retained-conversations"
    assert (home / ".env").read_text() == "EXISTING_SECRET=preserved\n"
    assert json.loads((home / "mithril-update.json").read_text())["highest_sequence"] == 7


def test_preparation_failure_never_selects_partial_runtime(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    old = checkout(tmp_path / "old", "old")
    monkeypatch.setattr(update, "SOURCE", Path(old["path"]))
    def failure(*_):
        raise update.UpdateError("partial install")
    with pytest.raises(update.UpdateError, match="partial"):
        update.apply({"runtime_revision": "a" * 40, "previous_revisions": [old["revision"]]}, home, {}, prepare_runtime=failure)
    assert not (home / "agent-current").exists()
    assert not (home / "mithril-update.json").exists()


def test_installer_digest_failure_executes_nothing(tmp_path, monkeypatch, release):
    payload, _, _ = release
    monkeypatch.setattr(update, "download", lambda *_: b"partial")
    def forbidden(*_, **__):
        pytest.fail("unverified installer executed")
    monkeypatch.setattr(update.subprocess, "run", forbidden)
    with pytest.raises(update.UpdateError, match="hash or length"):
        update.prepare(payload, tmp_path, tmp_path / "versions")
    assert not (tmp_path / "versions").exists()


def test_unprovisioned_root_makes_no_network_request(monkeypatch, tmp_path):
    trust_file = tmp_path / "trust.json"
    trust_file.write_text(json.dumps({"schema": 1, "repository": update.REPOSITORY, "channel": "preview", "keys": {}}))
    monkeypatch.setattr(update, "TRUST_FILE", trust_file)
    def forbidden(*_):
        pytest.fail("unprovisioned update attempted network")
    monkeypatch.setattr(update, "download", forbidden)
    with pytest.raises(update.UpdateError, match="signing root"):
        update.load_trust()


def test_no_mithril_request_reaches_hermes_channel(monkeypatch):
    from hermes_cli import source_releases, source_check
    monkeypatch.setenv("MITHRIL_AGENT_CLI_LAUNCHER", "1")
    monkeypatch.setattr(source_releases, "_resolve_channel", lambda *_: pytest.fail("Hermes channel accessed"))
    with pytest.raises(ValueError, match="Mithril"):
        source_releases.resolve_source_target("stable")
    assert source_check.check_for_updates()["reason"] == "mithril-distribution-updater"


@pytest.mark.parametrize("settings", [{"check": False}, {"auto_apply": False}])
def test_profile_update_preferences_are_respected(monkeypatch, settings):
    from hermes_cli import config
    monkeypatch.setenv("MITHRIL_INSTALL_HOME", "/selected/install")
    monkeypatch.delenv("MITHRIL_UPDATE_REEXEC", raising=False)
    monkeypatch.setattr(config, "load_config_readonly", lambda: {"updates": settings})
    calls = []
    monkeypatch.setattr(update, "run", lambda *a, **kw: calls.append((a, kw)) or {"version": "0.1.0-preview.7"})
    update.before_chat([])
    assert len(calls) == (0 if settings.get("check") is False else 1)
    if calls:
        assert calls[0][1] == {"check": True}
