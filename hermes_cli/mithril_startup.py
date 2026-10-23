"""Require device approval before the selected Profile's first Mithril chat."""

import os
import sys


def before_chat(argv=None):
    """Run after the CLI has resolved and removed its Profile selector."""
    if os.environ.get("MITHRIL_AGENT_CLI_LAUNCHER") != "1":
        return
    argv = list(sys.argv[1:] if argv is None else argv)
    from hermes_cli._parser import command_argv

    positional = command_argv(argv)
    if positional and positional[0] != "chat":
        return
    skip_flags = {
        "-h", "--help", "-V", "--version", "-q", "--query", "-z", "--oneshot",
        "--safe-mode", "--ignore-user-config", "--provider",
    }
    if any(arg.split("=", 1)[0] in skip_flags for arg in argv):
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return

    from hermes_cli import mithril_auth
    from hermes_cli.config import is_managed, load_env

    # main._apply_profile_override already selected the home. Resolving it again
    # would undo an explicit `-p default` when active_profile names another home.
    try:
        if is_managed() or load_env().get(mithril_auth.TOKEN_ENV, "").strip():
            return
    except (OSError, RuntimeError, ValueError):
        print("Mithril Agent: cannot read this Profile's sign-in settings. Run `mithril login` to check them.",
              file=sys.stderr)
        raise SystemExit(1) from None
    login_argv = ["login"]
    for index, arg in enumerate(argv):
        if arg in ("-m", "--model") and index + 1 < len(argv):
            login_argv.extend(["--model", argv[index + 1]])
        elif arg.startswith("--model="):
            login_argv.extend(["--model", arg.split("=", 1)[1]])
    result = mithril_auth.main(login_argv, continue_on_login=True, profile_selected=True)
    if result != 0:
        raise SystemExit(result)
