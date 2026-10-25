"""Device approval must precede chat and remain confined to its resolved Profile."""

import io
import json
import os
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hermes_cli import mithril_auth as auth, mithril_startup


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("outcome", ["approved", "access_denied", "expired_token", "cancelled", "unavailable"])
def test_device_approval_roundtrip_is_profile_local(tmp_path, monkeypatch, outcome):
    from hermes_cli import config
    import hermes_constants

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("MITHRIL_AGENT_CLI_LAUNCHER", "1")
    monkeypatch.setenv(auth.TOKEN_ENV, "inherited-other-profile-token")
    monkeypatch.setattr(sys, "stdin", Terminal())
    output = Terminal()
    monkeypatch.setattr(sys, "stdout", output)
    hermes_constants.pin_process_hermes_home(None)
    root = tmp_path / "profiles-root"
    root.mkdir()
    (root / "active_profile").write_text("other\n")
    homes = [root, root / "profiles" / "other"]
    for home in homes:
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text("# preserved\nmodel:\n  default: test/model\n")
        (home / ".env").write_text("OTHER_SECRET=preserved\n")
        (home / "state.db").write_bytes(b"existing-conversations")
    token = "mf_" + "a" * 43
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.respond()

        def respond(self):
            requests.append(self.path)
            status = 200
            if outcome == "unavailable":
                status, value = 503, {"error": "unavailable"}
            elif self.path == "/v1/models":
                value = {"data": [{"id": "test/model"}]}
            elif self.path == "/v1/device/code":
                value = {"device_code": "a" * 43, "user_code": "ABCD-EFGH",
                         "verification_uri": auth.CONSOLE,
                         "verification_uri_complete": auth.CONSOLE + "?user_code=ABCD-EFGH",
                         "expires_in": 40, "interval": 1}
            elif self.path == "/v1/device/token":
                if outcome == "approved":
                    value = {"access_token": token, "token_type": "Bearer",
                             "scope": " ".join(sorted(auth.SCOPES))}
                else:
                    status, value = 400, {"error": outcome}
            else:
                assert self.path == "/v1/me"
                assert self.headers["Authorization"] == "Bearer " + token
                value = {"user": {"id": "account"}, "scopes": list(auth.SCOPES)}
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setattr(auth, "API", f"http://127.0.0.1:{server.server_port}")
    opened = []
    monkeypatch.setattr(auth.webbrowser, "open", opened.append)
    original_poll = auth.poll

    def poll(grant):
        if outcome == "cancelled":
            raise KeyboardInterrupt
        return original_poll(grant, wait=lambda _: None)

    monkeypatch.setattr(auth, "poll", poll)
    try:
        # A -> B -> A exercises real config/environment writers and readers.
        for home in [*homes, homes[0]]:
            monkeypatch.setenv("HERMES_HOME", str(home))
            if outcome == "approved":
                mithril_startup.before_chat([])
                assert config.load_env()[auth.TOKEN_ENV] == token
            else:
                before = {p.name: p.read_bytes() for p in home.iterdir() if p.is_file()}
                with pytest.raises(SystemExit) as exit_info:
                    mithril_startup.before_chat([])
                assert exit_info.value.code == (130 if outcome == "cancelled" else 1)
                assert {p.name: p.read_bytes() for p in home.iterdir() if p.is_file()} == before
                assert auth.TOKEN_ENV not in config.load_env()
            assert config.load_env()["OTHER_SECRET"] == "preserved"
            assert (home / "state.db").read_bytes() == b"existing-conversations"
        if outcome == "approved":
            assert len(opened) == 2
            assert requests.count("/v1/me") == 2
            assert requests[:4] == ["/v1/device/code", "/v1/device/token", "/v1/me", "/v1/models"]
            assert "Continuing to your session." in output.getvalue()
        assert token not in output.getvalue()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_connector_login_keeps_inference_settings_without_model_lookup(tmp_path, monkeypatch):
    from hermes_cli import config
    import hermes_constants

    hermes_constants.pin_process_hermes_home(None)
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # Headless login must not ask for a model.
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    original_config = (
        "model:\n"
        "  provider: Other\n"
        "  default: other/model\n"
        "  base_url: https://other.example/v1\n"
        "providers:\n"
        "  Other:\n"
        "    name: Other\n"
        "    key_env: OTHER_SECRET\n"
    ).encode()
    (home / "config.yaml").write_bytes(original_config)
    (home / ".env").write_text("OTHER_SECRET=preserved\n")
    approved_token = "mf_" + "a" * 43
    requested_scopes = auth.SCOPES | auth.CONNECTOR_SCOPES
    requests = []

    def request(path, body=None, token=None):
        requests.append(path)
        if path == "/v1/device/code":
            assert body["scope"] == " ".join(sorted(requested_scopes))
            return {"device_code": "a" * 43, "user_code": "ABCD-EFGH",
                    "verification_uri": auth.CONSOLE,
                    "verification_uri_complete": auth.CONSOLE + "?user_code=ABCD-EFGH",
                    "expires_in": 40, "interval": 1}
        if path == "/v1/device/token":
            return {"access_token": approved_token, "token_type": "Bearer",
                    "scope": " ".join(sorted(requested_scopes))}
        if path == "/v1/me":
            assert token == approved_token
            return {"user": {"id": "account"}, "scopes": list(requested_scopes)}
        raise AssertionError(f"Unexpected request: {path}")

    monkeypatch.setattr(auth, "request", request)
    original_poll = auth.poll
    monkeypatch.setattr(auth, "poll", lambda grant, **kwargs: original_poll(
        grant, call=request, wait=lambda _: None, **kwargs))

    assert auth.main(["login", "--connectors", "--no-browser"], profile_selected=True) == 0
    assert requests == ["/v1/device/code", "/v1/device/token", "/v1/me"]
    assert (home / "config.yaml").read_bytes() == original_config
    assert config.load_env() == {"OTHER_SECRET": "preserved", auth.TOKEN_ENV: approved_token}


@pytest.mark.parametrize("argv,marker,tty,result,starts", [
    ([], True, True, 0, True),
    ([], True, True, 1, False),
    (["--reasoning", "high", "chat"], True, True, 130, False),
    (["--help"], True, True, 1, True),
    (["--query=hello"], True, True, 1, True),
    (["--provider=other"], True, True, 1, True),
    ([], False, True, 1, True),
    ([], True, False, 1, True),
])
def test_main_blocks_chat_until_startup_login_completes(tmp_path, monkeypatch, argv, marker, tty, result, starts):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["mithril", *argv])
    from hermes_cli import main as cli_main, boot_bootstrap, venv_sync, update_cmd_fleet
    from agent import ssl_verify

    monkeypatch.setattr(sys, "stdin", Terminal() if tty else io.StringIO())
    monkeypatch.setattr(sys, "stdout", Terminal())
    if marker:
        monkeypatch.setenv("MITHRIL_AGENT_CLI_LAUNCHER", "1")
    else:
        monkeypatch.delenv("MITHRIL_AGENT_CLI_LAUNCHER", raising=False)
    for name in ["_set_process_title", "_warn_if_unsupervised_pid1", "_advertise_agent_env",
                 "_cleanup_quarantined_exes", "_sweep_stale_bytecode_if_checkout_changed"]:
        monkeypatch.setattr(cli_main, name, lambda: None)
    monkeypatch.setattr(ssl_verify, "install_truststore", lambda: None)
    monkeypatch.setattr(boot_bootstrap, "maybe_run_boot_bootstrap", lambda *_: None)
    monkeypatch.setattr(venv_sync, "check_runtime", lambda *_: None)
    monkeypatch.setattr(update_cmd_fleet, "_warn_pending_fleet_restart_on_startup", lambda: None)
    events = []

    def login(*args, **kwargs):
        events.append("login")
        return result

    def chat():
        events.append("chat")
        return True

    monkeypatch.setattr(auth, "main", login)
    monkeypatch.setattr(cli_main, "_try_termux_fast_tui_launch", chat)
    if starts:
        cli_main.main()
        assert events == (["login", "chat"] if marker and tty and not argv else ["chat"])
    else:
        with pytest.raises(SystemExit) as exit_info:
            cli_main.main()
        assert exit_info.value.code == result
        assert events == ["login"]
