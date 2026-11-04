"""Mithril VPN CLI. Only public state crosses the native helper boundary."""
from __future__ import annotations

import datetime as dt
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

PROFILE_ID = re.compile(r"[a-f0-9]{32}")
STATES = {"unavailable", "disconnected", "connecting", "connected", "disconnecting", "unknown"}
PLATFORMS = {"darwin", "win32", "linux"}
REASONS = {None, "native-component-missing", "permission-required", "profile-required",
           "profile-expired", "operation-failed", "browser-unavailable"}


def public_state(value, platform):
    """Validate and project native output; do not print unknown fields or secrets."""
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Invalid VPN state")
    if platform == "darwin" and value.get("controllerVersion") != 2:
        raise ValueError("An updated Mithril VPN helper is required")
    if value.get("platform") != platform or value.get("state") not in STATES:
        raise ValueError("Invalid VPN state")
    if value.get("reason") not in REASONS or value.get("egressVerification") != "unverified":
        raise ValueError("Invalid VPN state")
    observed = value.get("observedAt")
    if not isinstance(observed, str):
        raise ValueError("Invalid VPN state")
    when = dt.datetime.fromisoformat(observed.replace("Z", "+00:00"))
    if when.tzinfo is None or abs((dt.datetime.now(dt.timezone.utc) - when).total_seconds()) > 15:
        raise ValueError("Stale VPN state")
    profiles = value.get("profiles")
    if not isinstance(profiles, list) or len(profiles) > 8:
        raise ValueError("Invalid VPN state")
    projected = []
    ids = set()
    for p in profiles:
        if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not PROFILE_ID.fullmatch(p["id"]) or p["id"] in ids:
            raise ValueError("Invalid VPN profile")
        if not isinstance(p.get("name"), str) or not 1 <= len(p["name"]) <= 80:
            raise ValueError("Invalid VPN profile")
        if not isinstance(p.get("region"), str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", p["region"]):
            raise ValueError("Invalid VPN profile")
        if p.get("egressMode") not in {"shared", "dedicated"}:
            raise ValueError("Invalid VPN profile")
        ipaddress.IPv4Address(p["expectedEgressIPv4"])
        expires = dt.datetime.fromisoformat(p["expiresAt"].replace("Z", "+00:00"))
        if expires.tzinfo is None:
            raise ValueError("Invalid VPN profile")
        ids.add(p["id"])
        clean = {k: p[k] for k in ("id", "name", "region", "egressMode", "expectedEgressIPv4", "expiresAt")}
        key = p.get("wireguardPublicKey")
        if key is not None:
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=", key):
                raise ValueError("Invalid VPN public key")
            clean["wireguardPublicKey"] = key
        projected.append(clean)
    active = value.get("activeProfileId")
    if active is not None and active not in ids:
        raise ValueError("Invalid VPN state")
    if value["state"] == "connected" and active is None:
        raise ValueError("Invalid VPN state")
    return {"version": 1, "platform": platform, "state": value["state"],
            "profiles": projected, "activeProfileId": active, "observedAt": observed,
            "egressVerification": "unverified", "reason": value["reason"]}


def native_command(platform=None):
    """Resolve packaged code only. Never search PATH or download executable code."""
    platform = platform or sys.platform
    root = Path(__file__).parent / "data" / "vpn"
    if platform == "linux":
        host = root / "linux_host.py"
        if host.is_file() and Path("/usr/bin/python3").is_file():
            return ["/usr/bin/python3", "-I", str(host)]
    elif platform == "darwin":
        candidates = [
            Path(sys.prefix) / "share/mithril/vpn/MithrilVPNHelper.app",
            Path("/Applications/Mithril.app/Contents/Resources/vpn/MithrilVPNHelper.app"),
        ]
        for bundle in candidates:
            helper = bundle / "Contents/MacOS/MithrilVPNHelper"
            if not helper.is_file():
                continue
            requirement = '=anchor apple generic and certificate leaf[subject.OU] = "3A5CBTEBFP" and identifier "fund.mithril.vpn-helper"'
            checked = subprocess.run(["/usr/bin/codesign", "--verify", "--strict", "-R", requirement, str(bundle)],
                                     capture_output=True, timeout=60, check=False)
            if checked.returncode == 0:
                return [str(helper)]
    # Windows publisher identity and standalone Apple helper packaging remain
    # release gates. Never execute an unsigned or guessed helper.
    raise RuntimeError("A qualified Mithril VPN system component is required on this installation.")


def call_native(request, command=None, platform=None):
    platform = platform or sys.platform
    if platform not in PLATFORMS:
        raise RuntimeError("VPN is unavailable on this operating system.")
    command = command or native_command(platform)
    env = {k: os.environ[k] for k in ("HOME", "USERPROFILE", "SystemRoot", "WINDIR",
           "LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "XDG_RUNTIME_DIR", "DISPLAY",
           "DBUS_SESSION_BUS_ADDRESS", "XAUTHORITY") if k in os.environ}
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    # Helper paths and imported profile contents never enter terminal output.
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(command, input=json.dumps(request).encode(), stdout=output,
                                stderr=subprocess.DEVNULL, env=env, timeout=40, check=False)
        output.seek(0)
        data = output.read(65537)
    if result.returncode or len(data) > 65536:
        raise RuntimeError("VPN outcome is unknown; refresh status before retrying.")
    return public_state(json.loads(data), platform)


def build_parser(subparsers):
    parser = subparsers.add_parser("vpn", help="Connect and manage Mithril VPN on this device")
    actions = parser.add_subparsers(dest="vpn_action", required=True)
    for action in ("status", "diagnostics", "connect", "disconnect", "import", "remove"):
        command = actions.add_parser(action)
        command.add_argument("--json", action="store_true", help="Print public VPN state as JSON")
        if action in {"connect", "remove"}:
            command.add_argument("profile_id", help="Profile ID from vpn status")
        if action == "import":
            command.add_argument("file", help="Mithril VPN profile; credentials stay native")
        if action == "remove":
            command.add_argument("--yes", action="store_true", help="Confirm removal of this profile")
        command.set_defaults(func=run)
    return parser


def run(args):
    action = args.vpn_action
    operations = {"status": "getState", "diagnostics": "getState", "connect": "connect",
                  "disconnect": "disconnect", "import": "importProfile", "remove": "forgetProfile"}
    request = {"operation": operations[action]}
    if action in {"connect", "remove"}:
        if not PROFILE_ID.fullmatch(args.profile_id):
            print("Invalid VPN profile ID.", file=sys.stderr)
            return 2
        request["profileId"] = args.profile_id
    if action == "remove" and not args.yes:
        print("Confirm profile removal with --yes.", file=sys.stderr)
        return 2
    if action == "import":
        try:
            path = Path(args.file).expanduser().resolve(strict=True)
            if not path.is_file() or not 2 <= path.stat().st_size <= 32768:
                raise ValueError()
        except (OSError, ValueError):
            print("VPN profile could not be imported.", file=sys.stderr)
            return 2
        request["importPath"] = str(path)
    try:
        # Always reconcile current OS state before mutating; no automatic replay.
        if action not in {"status", "diagnostics", "disconnect"}:
            current = call_native({"operation": "getState"})
            if current["state"] != "disconnected":
                raise RuntimeError()
            if action == "connect":
                p = next((p for p in current["profiles"] if p["id"] == args.profile_id), None)
                if not p or dt.datetime.fromisoformat(p["expiresAt"].replace("Z", "+00:00")) <= dt.datetime.now(dt.timezone.utc):
                    raise RuntimeError()
        state = call_native(request)
    except Exception:
        print("VPN operation could not be confirmed. Check the installed system component and run 'mithril vpn status' before retrying.", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(state, ensure_ascii=True))
    else:
        print("Mithril VPN: " + state["state"])
        for p in state["profiles"]:
            # JSON quoting prevents profile names from injecting terminal controls.
            print("  " + p["id"] + "  " + json.dumps(p["name"], ensure_ascii=True) + "  " + p["region"])
        print("Exit IP: unverified")
        if state["reason"]:
            print("Reason: " + state["reason"])
    return 0 if state["state"] not in {"unavailable", "unknown"} and state["reason"] not in {"operation-failed", "permission-required", "profile-expired"} else 1


def main(argv=None):
    """Run the VPN-only CLI without initializing Agent configuration or imports."""
    import argparse
    parser = argparse.ArgumentParser(prog="mithril")
    build_parser(parser.add_subparsers(dest="command", required=True))
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
