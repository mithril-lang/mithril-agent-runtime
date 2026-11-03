"""Explicit, bounded diagnostics and per-Profile error-report consent."""
import argparse
import json


def command(args):
    from hermes_cli import mithril_sentry as sentry
    if args.diagnostic == "sentry":
        return sentry.main()
    action = args.error_report_action
    if action == "status":
        print(json.dumps({"enabled": sentry.reporting_enabled()}))
        return 0
    from hermes_cli.config import read_raw_config, get_config_path, atomic_config_write
    data = read_raw_config()
    telemetry = data.setdefault("telemetry", {})
    if not isinstance(telemetry, dict):
        raise ValueError("Invalid telemetry configuration")
    reports = telemetry.setdefault("error_reports", {})
    if not isinstance(reports, dict):
        raise ValueError("Invalid error-report configuration")
    reports["enabled"] = action == "enable"
    atomic_config_write(get_config_path(), data)
    print(json.dumps({"enabled": reports["enabled"]}))
    return 0


def build_parser(subparsers):
    parser = subparsers.add_parser("diagnostics", help="Content-free Mithril diagnostics and error-report consent")
    groups = parser.add_subparsers(dest="diagnostic", required=True)
    diagnostic = groups.add_parser("sentry", help="Send one content-free Sentry diagnostic and print its event ID")
    diagnostic.set_defaults(func=command)
    consent = groups.add_parser("error-reports", help="Manage automatic error reports for this Profile")
    actions = consent.add_subparsers(dest="error_report_action", required=True)
    enable = actions.add_parser("enable", help="Explicitly consent to content-free automatic error reports")
    enable.add_argument("--error-reports-consent", action="store_true", required=True)
    enable.set_defaults(func=command)
    for name in ("disable", "status"):
        actions.add_parser(name).set_defaults(func=command)


def install_cli_hooks(environ=None):
    """Called at main dispatch after Profile and dotenv resolution, never at import."""
    import os
    env = os.environ if environ is None else environ
    if env.get("MITHRIL_AGENT_CLI_LAUNCHER") != "1" or env.get("MITHRIL_AGENT") != "1":
        return
    env.setdefault("MITHRIL_CLIENT", "cli")
    if env.get("MITHRIL_CLIENT") == "cli":
        from hermes_cli import mithril_sentry
        mithril_sentry.install()
