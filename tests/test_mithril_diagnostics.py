import argparse
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from hermes_cli import mithril_diagnostics as diagnostics
from hermes_cli import mithril_sentry as sentry

class DiagnosticsTests(unittest.TestCase):
    def parse(self, values):
        parser=argparse.ArgumentParser()
        diagnostics.build_parser(parser.add_subparsers(dest="command"))
        return parser.parse_args(values)

    def test_enable_requires_explicit_error_report_consent(self):
        with self.assertRaises(SystemExit):
            self.parse(["diagnostics","error-reports","enable"])
        self.assertTrue(self.parse(["diagnostics","error-reports","enable","--error-reports-consent"]).error_reports_consent)

    def test_profile_consent_write_preserves_unrelated_configuration_and_withdraws(self):
        from hermes_cli.config import atomic_config_write
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        with tempfile.TemporaryDirectory() as directory:
            homes=[Path(directory)/name for name in ("a","b")]
            for home in homes:
                home.mkdir()
                atomic_config_write(home/"config.yaml",{"provider":"retained-provider","telemetry":{"shared_metrics":{"enabled":False}}})
            for home,action,expected in [(homes[0],"enable",True),(homes[1],"status",False),(homes[0],"disable",False)]:
                token=set_hermes_home_override(home)
                try:
                    args=["diagnostics","error-reports",action]
                    if action=="enable":args+=["--error-reports-consent"]
                    output=io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(0,diagnostics.command(self.parse(args)))
                    self.assertEqual(expected,json.loads(output.getvalue())["enabled"])
                    from hermes_cli.config import read_raw_config
                    saved=read_raw_config()
                    self.assertEqual("retained-provider",saved["provider"])
                    self.assertFalse(saved["telemetry"]["shared_metrics"]["enabled"])
                finally:reset_hermes_home_override(token)

    def test_explicit_diagnostic_does_not_change_saved_consent(self):
        with patch.object(sentry,"main",return_value=1) as send:
            self.assertEqual(1,diagnostics.command(self.parse(["diagnostics","sentry"])))
            send.assert_called_once_with()

    def test_only_branded_cli_installs_hooks_and_an_explicit_other_surface_is_retained(self):
        for env,expected in [({},False),({"MITHRIL_AGENT":"1"},False),
            ({"MITHRIL_AGENT":"1","MITHRIL_AGENT_CLI_LAUNCHER":"1"},True),
            ({"MITHRIL_AGENT":"1","MITHRIL_AGENT_CLI_LAUNCHER":"1","MITHRIL_CLIENT":"desktop"},False)]:
            with patch.dict(os.environ,env,clear=True),patch.object(sentry,"install") as install:
                diagnostics.install_cli_hooks()
                self.assertEqual(expected,install.called)
                if expected:self.assertEqual("cli",os.environ["MITHRIL_CLIENT"])
                elif env.get("MITHRIL_CLIENT"):self.assertEqual("desktop",os.environ["MITHRIL_CLIENT"])

if __name__=="__main__":unittest.main()
