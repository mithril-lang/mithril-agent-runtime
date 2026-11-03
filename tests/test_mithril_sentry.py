import io
import json
import sys
import threading
import subprocess
import tempfile
from pathlib import Path
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from hermes_cli import mithril_sentry as sentry


class Response:
    status = 204
    def __enter__(self): return self
    def __exit__(self, *args): return False


class SentryTests(unittest.TestCase):
    def setUp(self):
        sentry._seen.clear()
        sentry._window = 0
        consent = patch.object(sentry, "reporting_enabled", return_value=True)
        consent.start()
        self.addCleanup(consent.stop)

    def test_payload_has_no_exception_message_or_external_path(self):
        try:
            raise ValueError("victim@example.com private prompt Bearer private-token")
        except ValueError as error:
            event = sentry.build_event(error, error.__traceback__)
        text = json.dumps(event)
        for value in ["victim@", "private prompt", "Bearer", "/Users/", "locals", "request"]:
            self.assertNotIn(value, text)
        self.assertEqual(event["tags"]["surface"], "cli")

    def test_provenance_is_bound_to_each_source_root_not_active_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            roots = [Path(directory) / name for name in ['a', 'b']]
            expected = []
            for root in roots:
                root.mkdir()
                subprocess.run(['git', 'init', '-q', str(root)], check=True)
                (root / 'source').write_text(root.name)
                subprocess.run(['git', '-C', str(root), 'add', 'source'], check=True)
                subprocess.run(['git', '-C', str(root), '-c', 'user.name=Mithril QA', '-c', 'user.email=qa@mithril.invalid', '-c', 'commit.gpgsign=false', 'commit', '-qm', 'QA source'], check=True)
                expected.append(subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip())
            with patch.dict(sentry.os.environ, {'HERMES_REPO_ROOT': str(roots[1])}):
                self.assertEqual(sentry.reporting_revision(roots[0]), expected[0])
                self.assertEqual(sentry.reporting_revision(roots[1]), expected[1])
                self.assertEqual(sentry.reporting_revision(roots[0]), expected[0])

    def test_fixed_tunnel_no_auth_and_preserved_event_id(self):
        captured = []
        def opener(request, timeout):
            captured.append(request)
            self.assertEqual(timeout, 2)
            return Response()
        error = RuntimeError("private")
        event_id = sentry.capture(error, opener=opener)
        request = captured[0]
        self.assertEqual(request.full_url, sentry.TUNNEL)
        self.assertIsNone(request.get_header("Authorization"))
        self.assertIsNone(request.get_header("Cookie"))
        data = list(map(json.loads, request.data.decode().splitlines()))
        self.assertEqual(data[0]["event_id"], event_id)
        self.assertEqual(data[2]["event_id"], event_id)
        self.assertNotIn("private", request.data.decode())
        self.assertIsNone(sentry.capture(error, opener=opener))
        self.assertEqual(len(captured), 1)

    def test_network_failure_and_normal_exit_never_raise(self):
        def offline(*args, **kwargs): raise OSError("private proxy error")
        self.assertIsNone(sentry.capture(RuntimeError("original"), opener=offline))
        for value in [KeyboardInterrupt(), SystemExit(1)]:
            self.assertIsNone(sentry.capture(value, opener=offline))

    def test_sends_at_most_five_distinct_errors_per_minute(self):
        calls = []
        def opener(*args, **kwargs): calls.append(1); return Response()
        for _ in range(12): sentry.capture(RuntimeError("different"), opener=opener)
        self.assertEqual(len(calls), 5)

    def test_unbranded_runtime_does_not_install_hooks(self):
        with patch.dict(sentry.os.environ, {"MITHRIL_CLIENT": ""}), patch.object(sentry, "_installed", False):
            before = sys.excepthook
            sentry.install()
            self.assertIs(sys.excepthook, before)

    def test_branded_hook_preserves_original_exception_policy(self):
        original, original_thread = sys.excepthook, threading.excepthook
        previous = unittest.mock.Mock()
        try:
            sys.excepthook = previous
            with patch.dict(sentry.os.environ, {"MITHRIL_CLIENT": "cli"}), patch.object(sentry, "_installed", False), patch.object(sentry, "capture") as capture:
                sentry.install()
                error = ValueError("test")
                sys.excepthook(ValueError, error, None)
                capture.assert_called_once_with(error, None)
                previous.assert_called_once_with(ValueError, error, None)
        finally:
            sys.excepthook, threading.excepthook = original, original_thread

    def test_diagnostic_reports_only_event_id(self):
        output = io.StringIO()
        with patch.object(sentry, "capture", return_value="a" * 32), redirect_stdout(output):
            self.assertEqual(sentry.main(), 0)
        self.assertEqual(json.loads(output.getvalue()), {"sentryEventId": "a" * 32})


class ConsentTests(unittest.TestCase):
    def test_real_profile_consent_defaults_off_and_follows_a_b_a_and_withdrawal(self):
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        from hermes_cli.config import atomic_config_write

        original, original_thread = sys.excepthook, threading.excepthook
        calls = []
        previous = unittest.mock.Mock()
        def opener(request, timeout):
            calls.append(request)
            return Response()
        try:
            with tempfile.TemporaryDirectory() as directory:
                roots = [Path(directory) / name for name in ["unset", "a", "b"]]
                for home, enabled in zip(roots, [None, True, False]):
                    home.mkdir()
                    data = {} if enabled is None else {"telemetry": {"error_reports": {"enabled": enabled}}}
                    atomic_config_write(home / "config.yaml", data)
                sys.excepthook = previous
                with patch.dict(sentry.os.environ, {"MITHRIL_CLIENT": "cli", "HERMES_HOME": str(roots[0])}), patch.object(sentry, "_installed", False), patch.object(sentry, "reporting_revision", return_value="a" * 40), patch.object(sentry.urllib.request, "build_opener") as transport:
                    transport.return_value.open = opener
                    sentry._seen.clear()
                    sentry._window = 0
                    sentry.install()
                    for home, expected in zip([roots[0], roots[1], roots[2], roots[1]], [0, 1, 1, 2]):
                        token = set_hermes_home_override(home)
                        try:
                            sys.excepthook(ValueError, ValueError("private fixture"), None)
                            self.assertEqual(len(calls), expected)
                        finally:
                            reset_hermes_home_override(token)
                    atomic_config_write(roots[1] / "config.yaml", {"telemetry": {"error_reports": {"enabled": False}}})
                    token = set_hermes_home_override(roots[1])
                    try:
                        sys.excepthook(ValueError, ValueError("after withdrawal"), None)
                        self.assertEqual(len(calls), 2)
                    finally:
                        reset_hermes_home_override(token)
                    self.assertEqual(previous.call_count, 5)
        finally:
            sys.excepthook, threading.excepthook = original, original_thread



if __name__ == "__main__":
    unittest.main()
