"""The launcher identity also owns startup recovery and command hints."""

import contextlib
import io
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_cli.branding import cli_name, product_name
from hermes_cli.shared_profile_warning import shared_profile_warning
from hermes_cli.tool_availability_notices import tool_availability_warning_lines


class StartupBrandingTests(unittest.TestCase):
    def test_notices_follow_launcher_identity_without_hiding_diagnostics(self):
        unavailable = [
            {"name": "terminal", "env_vars": []},
            {"name": "web", "env_vars": ["EXA_API_KEY"]},
            {"name": "x_search", "env_vars": ["XAI_API_KEY"]},
        ]
        for flag, command, product in [("1", "mithril", "Mithril"), ("0", "hermes", "Hermes"),
                                       ("1", "mithril", "Mithril")]:
            with self.subTest(command=command), patch.dict(os.environ, MITHRIL_AGENT=flag):
                self.assertEqual(cli_name(), command)
                self.assertEqual(product_name(), product)
                lines = "\n".join(tool_availability_warning_lines(
                    unavailable, terminal_reason="daemon is not running", terminal_backend="docker"))
                for hint in ["doctor", "setup terminal", "setup tools"]:
                    self.assertIn(f"{command} {hint}", lines)
                self.assertIn("daemon is not running", lines)
                self.assertIn("XAI_API_KEY", lines)
                other_command = "hermes" if command == "mithril" else "mithril"
                self.assertNotIn(other_command, lines.lower())
                with patch("hermes_cli.shared_profile_warning.install_id", return_value="own"), \
                     patch("hermes_cli.shared_profile_warning.hermes_home_key", return_value="profile"), \
                     patch("hermes_cli.shared_profile_warning.ledger_entries", return_value=[
                         {"install": "other", "hermes_home": "profile"}]):
                    warning = shared_profile_warning(home=Path("profile"))
                self.assertIn(f"Another {product} installation", warning)
                self.assertIn("changes can conflict", warning)

    def test_dependency_recovery_uses_launcher_name_before_sync(self):
        from hermes_cli import venv_sync

        class StopBeforeSync(Exception):
            pass

        for flag, command in [("1", "mithril"), ("0", "hermes")]:
            output = io.StringIO()
            with self.subTest(command=command), patch.dict(os.environ, MITHRIL_AGENT=flag), \
                 patch("hermes_cli._early_recovery._marker_owner_is_live", return_value=False), \
                 patch.object(venv_sync, "_sync_source_dependencies", side_effect=StopBeforeSync), \
                 contextlib.redirect_stderr(output):
                with self.assertRaises(StopBeforeSync):
                    venv_sync._finish_source_update(Path("unused"), current=False, pending=Path("pending"))
            self.assertIn(f"{command}: completing source-update dependencies", output.getvalue())


if __name__ == "__main__":
    unittest.main()
