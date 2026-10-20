"""Mithril CLI startup policy: opt-in runtime updates before interactive sessions."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Any, Callable


LAUNCHER_MARKER = "MITHRIL_AGENT_CLI_LAUNCHER"


def _profile_from_argv(argv: list[str]) -> str | None:
    profile = None
    for index, arg in enumerate(argv):
        if arg in ("-p", "--profile") and index + 1 < len(argv):
            profile = argv[index + 1]
        elif arg.startswith("--profile="):
            profile = arg.split("=", 1)[1]
    return profile


def _interactive_chat_invocation(argv: list[str], is_tty: Callable[[], bool] | None = None) -> bool:
    from hermes_cli._parser import command_argv

    positional = command_argv(argv)
    first = positional[0] if positional else None
    if first not in (None, "chat"):
        return False
    if any(arg in {
        "-h", "--help", "-V", "--version", "-q", "--query", "-z", "--oneshot",
        "--safe-mode", "--ignore-user-config",
    } for arg in argv):
        return False
    if any(arg.startswith("--query=") for arg in argv):
        return False
    if any(arg == "--provider" or arg.startswith("--provider=") for arg in argv):
        return False
    if is_tty is not None:
        try:
            return bool(is_tty())
        except Exception:
            return False
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:
        return False


def maybe_auto_update(
    argv: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    load_config: Callable[[], dict[str, Any]] | None = None,
    check_updates: Callable[..., dict[str, Any]] | None = None,
    run: Callable[..., Any] = subprocess.run,
    reexec: Callable[..., Any] = os.execv,
    tty: Callable[[], bool] | None = None,
    executable: str | None = None,
) -> None:
    """Apply an enabled CLI update before a new interactive session, then restart.

    The launcher marker keeps this behavior out of ordinary Hermes CLI runs. User
    preference lives in config.yaml under ``updates.auto_apply``. Update checks are
    passive and fail open; once an update starts, a failed apply stops this launch.
    """
    env = os.environ if env is None else env
    if env.get(LAUNCHER_MARKER) != "1":
        return
    argv = list(sys.argv[1:] if argv is None else argv)
    if not _interactive_chat_invocation(argv, is_tty=tty):
        return

    try:
        from hermes_cli.mithril_auth import select_home
        select_home(_profile_from_argv(argv))
        if load_config is None:
            from hermes_cli.config import load_config_readonly
            load_config = load_config_readonly
        config = load_config()
        updates = config.get("updates", {}) if isinstance(config, dict) else {}
        if not isinstance(updates, dict) or updates.get("auto_apply") is not True:
            return
        if check_updates is None:
            from hermes_cli.source_check import check_for_updates
            check_updates = check_for_updates
        status = check_updates(passive=True)
    except Exception:
        return

    behind = status.get("behind") if isinstance(status, dict) else None
    update_available = status.get("updateAvailable") is True if isinstance(status, dict) else False
    if (not isinstance(status, dict) or status.get("supported") is not True
            or status.get("dirty") is True or not isinstance(behind, int)
            or isinstance(behind, bool) or not (behind > 0 or update_available)):
        return

    if behind > 0:
        update_label = f"{behind} {'commit' if behind == 1 else 'commits'} behind"
    else:
        update_label = "a newer release is available"
    print(f"Mithril Agent: applying an update ({update_label}) before starting your session.", flush=True)
    command = executable or sys.argv[0]
    if os.path.sep not in command:
        command = shutil.which(command) or command
    update_argv = [command]
    profile = _profile_from_argv(argv)
    if profile:
        update_argv.extend(["--profile", profile])
    update_argv.extend(["update", "--yes"])
    try:
        result = run(update_argv, check=False)
    except Exception:
        print("Mithril Agent: the update could not start; your session was not started.", file=sys.stderr)
        raise SystemExit(1) from None
    code = getattr(result, "returncode", 1)
    if code != 0:
        raise SystemExit(code if isinstance(code, int) else 1)
    try:
        reexec(command, [command, *argv])
    except OSError:
        print("Mithril Agent: the update finished, but the CLI could not restart. Run `mithril` again.", file=sys.stderr)
        raise SystemExit(1) from None
