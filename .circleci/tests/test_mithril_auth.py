"""Mithril device approval must finish before account credentials affect a Profile."""
import os
import threading
from pathlib import Path
import stat
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hermes_cli import mithril_auth as auth


def grant():
    return {"device_code": "a" * 43, "user_code": "ABCD-EFGH",
            "verification_uri": auth.CONSOLE,
            "verification_uri_complete": auth.CONSOLE + "?user_code=ABCD-EFGH",
            "expires_in": 40, "interval": 5}


def test_poll_obeys_backoff_and_never_approves_its_own_device():
    clock = [0]
    delays, calls = [], []
    replies = iter([{"error": "authorization_pending"}, {"error": "slow_down"},
                    {"access_token": "mf_" + "a" * 43, "token_type": "Bearer",
                     "scope": "workspace:read inference"}])
    def wait(delay):
        delays.append(delay)
        clock[0] += delay
    def call(path, body):
        calls.append((path, body))
        return next(replies)
    assert auth.poll(grant(), call, lambda: clock[0], wait) == "mf_" + "a" * 43
    assert delays == [5, 5, 10]
    assert calls == [("/v1/device/token", {"device_code": "a" * 43})] * 3


@pytest.mark.parametrize("change", [
    {"verification_uri_complete": "https://evil.example/?user_code=ABCD-EFGH"},
    {"verification_uri_complete": auth.CONSOLE + "?user_code=ZZZZ-ZZZZ"},
    {"interval": 0}, {"expires_in": float("nan")}, {"expires_in": 100000},
])
def test_invalid_handoff_cannot_open_browser(change):
    with pytest.raises(auth.SignInError):
        auth.validate_grant({**grant(), **change})


@pytest.mark.parametrize("response", [
    {"error": "access_denied"}, {"error": "expired_token"},
    {"access_token": "mf_" + "a" * 43, "token_type": "Bearer", "scope": "inference"},
])
def test_unapproved_or_incomplete_grant_never_yields_credential(response):
    with pytest.raises(auth.SignInError):
        auth.poll(grant(), lambda *_: response, lambda: 0, lambda _: None)


def test_login_roundtrip_uses_real_config_writer_and_profile_secret_scope(tmp_path, monkeypatch, capsys):
    from hermes_cli import config
    import hermes_constants
    root = tmp_path / "mithril"
    root.mkdir()
    selected = root / "profiles" / "work"
    selected.mkdir(parents=True)
    (selected / "config.yaml").write_text("# Retain this comment\nmodel:\n  default: test/model\n  provider: Other\n  api_key: prior-inline-key\n  base_url: https://other.example/v1\nterminal:\n  cwd: /keep\n")
    (selected / ".env").write_text("MITHRIL_API_KEY=existing-api-key\nOTHER_SECRET=retained\n")
    (root / ".env").write_text("MITHRIL_API_KEY=other-profile-key\n")
    (selected / "state.db").write_bytes(b"existing-conversation")
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    hermes_constants.pin_process_hermes_home(None)
    token = "mf_" + "b" * 43
    calls = []
    def call(path, body=None, token=None):
        calls.append(path)
        if path == "/v1/models":
            return {"data": [{"id": "test/model"}]}
        if path == "/v1/device/code":
            assert body == {"device_name": "Mithril Agent CLI", "scope": "inference workspace:read"}
            return grant()
        assert path == "/v1/me"
        return {"user": {"id": "owner"}, "scopes": list(auth.SCOPES)}
    monkeypatch.setattr(auth, "request", call)
    monkeypatch.setattr(auth, "poll", lambda _: token)
    opened = []
    monkeypatch.setattr(auth.webbrowser, "open", opened.append)
    assert auth.main(["-p", "work", "login"]) == 0
    assert opened == [grant()["verification_uri_complete"]]
    assert token not in capsys.readouterr().out
    assert config.load_env()[auth.TOKEN_ENV] == token
    assert config.load_env()["MITHRIL_API_KEY"] == "existing-api-key"
    assert (root / ".env").read_text() == "MITHRIL_API_KEY=other-profile-key\n"
    assert (selected / "state.db").read_bytes() == b"existing-conversation"
    saved = config.read_user_config_raw()
    assert saved["model"]["provider"] == "Mithril Account"
    assert saved["model"]["base_url"] == auth.API + "/v1"
    assert "api_key" not in saved["model"]
    snapshot = next((selected / "backups").glob("before-mithril-login-*.yaml"))
    assert "prior-inline-key" in snapshot.read_text()
    assert saved["providers"]["Mithril Account"]["key_env"] == auth.TOKEN_ENV
    assert saved["terminal"]["cwd"] == "/keep"
    assert "# Retain this comment" in (selected / "config.yaml").read_text()
    from hermes_cli.runtime_provider_custom import _match_new_style_provider
    provider = _match_new_style_provider("mithril-account", saved["providers"])
    assert provider["base_url"] == auth.API + "/v1"
    assert provider["api_key"] == token
    if os.name == "posix":
        assert stat.S_IMODE((selected / ".env").stat().st_mode) == 0o600
    assert auth.main(["account", "status"]) == 0
    assert auth.main(["logout"]) == 0
    assert auth.TOKEN_ENV not in config.load_env()
    assert config.load_env()["MITHRIL_API_KEY"] == "existing-api-key"
    assert auth.main(["account", "status"]) == 1


def test_declined_login_keeps_previous_configuration(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("model:\n  default: test/model\n")
    before = (tmp_path / "config.yaml").read_bytes()
    def call(path, body=None):
        return {"data": [{"id": "test/model"}]} if path == "/v1/models" else grant()
    monkeypatch.setattr(auth, "request", call)
    monkeypatch.setattr(auth, "poll", lambda _: (_ for _ in ()).throw(auth.SignInError("Sign-in was declined.")))
    assert auth.main(["login", "--no-browser"]) == 1
    assert (tmp_path / "config.yaml").read_bytes() == before
    assert not (tmp_path / ".env").exists()
    assert "declined" in capsys.readouterr().err


def test_sticky_profile_and_explicit_default_do_not_cross_account_boundaries(tmp_path, monkeypatch):
    root = tmp_path / "mithril"
    named = root / "profiles" / "work"
    named.mkdir(parents=True)
    (named / "config.yaml").write_text("{}\n")
    (root / "active_profile").write_text("work\n")
    monkeypatch.setenv("HERMES_HOME", str(root))
    auth.select_home()
    assert Path(os.environ["HERMES_HOME"]) == named
    auth.select_home("default")
    assert Path(os.environ["HERMES_HOME"]) == root


def test_config_write_failure_restores_previous_login_token(tmp_path, monkeypatch):
    from hermes_cli import config
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("model:\n  default: old-model\n")
    (tmp_path / ".env").write_text(auth.TOKEN_ENV + "=old-account\nMITHRIL_API_KEY=preserved\n")
    monkeypatch.setattr(config, "atomic_config_write", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        auth.persist("mf_" + "b" * 43, "new/model")
    assert config.load_env() == {auth.TOKEN_ENV: "old-account", "MITHRIL_API_KEY": "preserved"}
    assert "old-model" in (tmp_path / "config.yaml").read_text()


def test_http_errors_are_sanitized_and_redirects_do_not_forward_credentials(monkeypatch):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers["User-Agent"] == "Mithril-Agent-CLI/1.0"
            received.append(self.path)
            if self.path == "/redirect":
                self.send_response(307)
                self.send_header("Location", "/credential-leak")
            else:
                self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error":"sensitive-response-body"}')
        def log_message(self, *_):
            pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setattr(auth, "API", f"http://127.0.0.1:{server.server_port}")
    try:
        for path in ("/redirect", "/unauthorized"):
            with pytest.raises(auth.SignInError) as error:
                auth.request(path, token="fixture-account-token")
            assert "sensitive-response-body" not in str(error.value)
        assert received == ["/redirect", "/unauthorized"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize("argv", [
    ["auth", "status", "openrouter"], ["-p", "work", "auth", "logout", "nous"],
    ["logout", "--provider", "spotify"], ["login", "--provider=anthropic"],
])
def test_existing_explicit_provider_auth_is_delegated_unchanged(argv, monkeypatch):
    calls = []
    def run_module(module, **kwargs):
        calls.append((module, list(sys.argv[1:])))
    monkeypatch.setattr(auth.runpy, "run_module", run_module)
    assert auth.main(argv) == 0
    assert calls == [("hermes_cli.main", argv)]
