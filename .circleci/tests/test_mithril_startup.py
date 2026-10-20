"""Mithril CLI launcher login and update behavior."""

from types import SimpleNamespace

from hermes_cli import mithril_auth, mithril_startup


def test_startup_login_uses_only_selected_profile_token(monkeypatch):
    selected = []
    monkeypatch.setattr(mithril_auth, "select_home", selected.append)
    from hermes_cli import config
    monkeypatch.setattr(config, "is_managed", lambda: False)
    monkeypatch.setattr(config, "load_env", lambda: {mithril_auth.TOKEN_ENV: "mf_profile-token"})
    called = []
    monkeypatch.setattr(mithril_auth, "main", lambda *a, **kw: called.append((a, kw)) or 1)

    assert mithril_auth.startup_login(["--profile", "work", "--model", "org/model"]) == 0
    assert selected == ["work"]
    assert called == []


def test_startup_login_starts_device_flow_for_selected_profile(monkeypatch):
    selected = []
    monkeypatch.setattr(mithril_auth, "select_home", selected.append)
    from hermes_cli import config
    monkeypatch.setattr(config, "is_managed", lambda: False)
    monkeypatch.setattr(config, "load_env", lambda: {})
    calls = []
    monkeypatch.setattr(mithril_auth, "main", lambda argv, **kw: calls.append((argv, kw)) or 0)

    assert mithril_auth.startup_login(["-p", "work", "--model=org/model"]) == 0
    assert selected == ["work"]
    assert calls == [(["login", "--profile", "work", "--model", "org/model"], {"continue_on_login": True})]


def test_auto_update_applies_before_chat_and_reexecutes(monkeypatch):
    from hermes_cli import config
    selected = []
    monkeypatch.setattr(mithril_auth, "select_home", selected.append)
    monkeypatch.setattr(config, "load_config_readonly", lambda: {"updates": {"auto_apply": True}})
    events = []

    def run(command, *, check):
        events.append(("run", command, check))
        return SimpleNamespace(returncode=0)

    def reexec(path, argv):
        events.append(("exec", path, argv))

    mithril_startup.maybe_auto_update(
        ["--profile", "work"],
        env={mithril_startup.LAUNCHER_MARKER: "1"},
        check_updates=lambda **kwargs: {"supported": True, "dirty": False, "behind": 2},
        run=run, reexec=reexec, tty=lambda: True, executable="/opt/homebrew/bin/hermes",
    )

    assert selected == ["work"]
    assert events == [
        ("run", ["/opt/homebrew/bin/hermes", "--profile", "work", "update", "--yes"], False),
        ("exec", "/opt/homebrew/bin/hermes", ["/opt/homebrew/bin/hermes", "--profile", "work"]),
    ]


def test_auto_update_is_launcher_only_opt_in_and_clean_checkout_only(monkeypatch):
    from hermes_cli import config
    monkeypatch.setattr(mithril_auth, "select_home", lambda _: None)
    monkeypatch.setattr(config, "load_config_readonly", lambda: {"updates": {"auto_apply": True}})
    calls = []
    common = dict(
        check_updates=lambda **kwargs: {"supported": True, "dirty": False, "behind": 1},
        run=lambda *a, **kw: calls.append("run") or SimpleNamespace(returncode=0),
        reexec=lambda *a: calls.append("exec"), tty=lambda: True, executable="hermes",
    )
    mithril_startup.maybe_auto_update([], env={}, **common)
    mithril_startup.maybe_auto_update(
        ["doctor"], env={mithril_startup.LAUNCHER_MARKER: "1"}, **common)
    mithril_startup.maybe_auto_update(
        [], env={mithril_startup.LAUNCHER_MARKER: "1"},
        check_updates=lambda **kwargs: {"supported": True, "dirty": True, "behind": 5},
        run=common["run"], reexec=common["reexec"], tty=common["tty"], executable="hermes",
    )
    mithril_startup.maybe_auto_update(
        [], env={mithril_startup.LAUNCHER_MARKER: "1"},
        load_config=lambda: {"updates": {"auto_apply": False}},
        check_updates=common["check_updates"], run=common["run"],
        reexec=common["reexec"], tty=common["tty"], executable="hermes",
    )
    assert calls == []


def test_failed_auto_update_stops_startup(monkeypatch):
    from hermes_cli import config
    monkeypatch.setattr(mithril_auth, "select_home", lambda _: None)
    monkeypatch.setattr(config, "load_config_readonly", lambda: {"updates": {"auto_apply": True}})
    try:
        mithril_startup.maybe_auto_update(
            [], env={mithril_startup.LAUNCHER_MARKER: "1"},
            check_updates=lambda **kwargs: {"supported": True, "dirty": False, "behind": 1},
            run=lambda *a, **kw: SimpleNamespace(returncode=2),
            reexec=lambda *a: None, tty=lambda: True, executable="hermes",
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("failed update must not continue into an interactive session")


def test_auto_update_applies_when_release_is_available_but_count_is_unknown(monkeypatch):
    from hermes_cli import config
    monkeypatch.setattr(mithril_auth, "select_home", lambda _: None)
    monkeypatch.setattr(config, "load_config_readonly", lambda: {"updates": {"auto_apply": True}})
    events = []
    mithril_startup.maybe_auto_update(
        [], env={mithril_startup.LAUNCHER_MARKER: "1"},
        check_updates=lambda **kwargs: {
            "supported": True, "dirty": False, "behind": -1, "updateAvailable": True,
        },
        run=lambda command, **kwargs: events.append(("run", command)) or SimpleNamespace(returncode=0),
        reexec=lambda path, argv: events.append(("exec", path, argv)),
        tty=lambda: True, executable="hermes",
    )
    import shutil
    assert events[0] == ("run", [shutil.which("hermes") or "hermes", "update", "--yes"])
    assert events[1][0] == "exec"


def test_auto_update_does_not_start_chat_if_update_process_cannot_start(monkeypatch):
    from hermes_cli import config
    monkeypatch.setattr(mithril_auth, "select_home", lambda _: None)
    monkeypatch.setattr(config, "load_config_readonly", lambda: {"updates": {"auto_apply": True}})
    try:
        mithril_startup.maybe_auto_update(
            [], env={mithril_startup.LAUNCHER_MARKER: "1"},
            check_updates=lambda **kwargs: {
                "supported": True, "dirty": False, "behind": 1, "updateAvailable": True,
            },
            run=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("not found")),
            reexec=lambda *args: None, tty=lambda: True, executable="hermes",
        )
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("a failed update launch must not enter chat")


def test_auto_update_defaults_on_for_mithril_and_keeps_explicit_cli_guidance(monkeypatch):
    import argparse
    from hermes_cli import config
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    from hermes_cli.subcommands.update import build_update_parser

    assert DEFAULT_CONFIG["updates"]["auto_apply"] is True
    monkeypatch.setenv(mithril_startup.LAUNCHER_MARKER, "1")
    assert config.recommended_update_command_for_method("git") == "mithril update"
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    build_update_parser(subparsers, cmd_update=lambda args: None)
    assert "Mithril Agent" in parser.format_help()
