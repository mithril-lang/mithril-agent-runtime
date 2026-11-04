"""Standalone VPN parser, output-custody and failure tests (no tunnel mutations)."""
import argparse
import contextlib
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

# Import the standalone command without bootstrapping the Agent or its renderer.
spec = importlib.util.spec_from_file_location("mithril_vpn", Path(__file__).parents[2] / "hermes_cli/vpn.py")
vpn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vpn)


def snapshot(**changes):
    value = {"version": 1, "platform": "linux", "state": "disconnected", "profiles": [{
        "id": "a" * 32, "name": "Mithril Tokyo", "region": "tokyo", "egressMode": "shared",
        "expectedEgressIPv4": "203.0.113.20", "expiresAt": "2099-01-01T00:00:00Z"}],
        "activeProfileId": None, "observedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "egressVerification": "unverified", "reason": None}
    value.update(changes)
    return value


def args(*argv):
    parser = argparse.ArgumentParser()
    vpn.build_parser(parser.add_subparsers())
    return parser.parse_args(["vpn", *argv])


class VpnCommandTests(unittest.TestCase):
    def test_public_projection_drops_secrets_at_both_levels(self):
        state = snapshot(password="private", nativePath="/private/key")
        state["profiles"][0]["wireguardConfig"] = "private"
        self.assertNotIn("private", json.dumps(vpn.public_state(state, "linux")))

    def test_stale_or_cross_platform_connected_state_is_rejected(self):
        for value in [snapshot(observedAt="2020-01-01T00:00:00Z"), snapshot(platform="darwin"),
                      snapshot(state="connected"), snapshot(activeProfileId="b" * 32)]:
            with self.assertRaises(ValueError):
                vpn.public_state(value, "linux")

    def test_connect_reconciles_before_mutation_and_does_not_replay(self):
        observed = snapshot()
        with patch.object(vpn, "call_native", side_effect=[observed, RuntimeError("private-key")]) as call:
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                self.assertEqual(vpn.run(args("connect", "a" * 32)), 1)
            self.assertEqual(call.call_count, 2)
            self.assertEqual(call.call_args_list[0].args[0], {"operation": "getState"})
            self.assertNotIn("private-key", output.getvalue())

    def test_unknown_state_prevents_connect(self):
        with patch.object(vpn, "call_native", return_value=snapshot(state="unknown")) as call:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(vpn.run(args("connect", "a" * 32)), 1)
            self.assertEqual(call.call_count, 1)

    def test_removal_requires_explicit_confirmation(self):
        with patch.object(vpn, "call_native") as call:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(vpn.run(args("remove", "a" * 32)), 2)
            call.assert_not_called()

    def test_terminal_profile_name_cannot_inject_escapes(self):
        state = snapshot()
        state["profiles"][0]["name"] = chr(27) + "[31m"
        with patch.object(vpn, "call_native", return_value=state):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(vpn.run(args("status")), 0)
            self.assertNotIn(chr(27), out.getvalue())

    def test_linux_standalone_uses_the_bundled_host_and_fixed_python(self):
        command = vpn.native_command("linux")
        self.assertEqual(command[0:2], ["/usr/bin/python3", "-I"])
        self.assertTrue(command[2].endswith("data/vpn/linux_host.py"))

    def test_apple_requires_the_serialized_controller_generation(self):
        value = snapshot(platform="darwin")
        with self.assertRaises(ValueError):
            vpn.public_state(value, "darwin")
        value["controllerVersion"] = 2
        self.assertEqual(vpn.public_state(value, "darwin")["state"], "disconnected")

    def test_no_shell_or_untrusted_native_error_output(self):
        with patch.object(vpn, "native_command", return_value=["/native/helper"]), patch.object(vpn.subprocess, "run", side_effect=TimeoutError("secret")) as run:
            with contextlib.redirect_stderr(io.StringIO()) as out:
                self.assertEqual(vpn.run(args("status", "--json")), 1)
            self.assertNotIn("secret", out.getvalue())
            self.assertNotIn("shell", run.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
