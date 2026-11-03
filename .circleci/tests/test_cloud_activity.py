import argparse
import unittest
from unittest.mock import patch

from hermes_cli.cloud_activity import normalize, show


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.response = {
            "schemaVersion": 1,
            "placement": {"state": "observed", "observedAt": 1000, "value": "cloud"},
            "container": {"state": "unavailable"},
            "alarm": {"state": "unavailable"},
            "checkpoint": {"state": "unavailable"},
            "cron": {"state": "observed", "observedAt": 1000, "value": {
                "result": "ok", "lastRunAt": 900, "nextRunAt": 2000,
            }},
        }

    def test_preserves_separate_business_result_and_stale_state(self):
        result = normalize(self.response, 2000)
        self.assertEqual(result["cron"]["result"], "ok")
        self.assertEqual(result["container"], "unavailable")
        stale = normalize(self.response, 1000 + 900001)
        self.assertEqual(stale["cron"], {"result": "unknown", "lastRunAt": None, "nextRunAt": None,
            "lastObserved": {"result": "ok", "lastRunAt": 900, "observedAt": 1000}})
        self.assertEqual(stale["placement"], "unknown")
        self.response["cron"]["value"]["lastRunAt"] = 1001
        self.assertNotIn("lastObserved", normalize(self.response, 1000 + 900001)["cron"])
        self.response["cron"]["value"].update(result="running", lastRunAt=900)
        self.assertNotIn("lastObserved", normalize(self.response, 1000 + 900001)["cron"])
        self.response["cron"]["value"].update(result="ok")
        self.assertNotIn("lastObserved", normalize(self.response, 999)["cron"])

    def test_rejects_unexpected_fields_and_malformed_values(self):
        self.response["cron"]["value"]["private"] = "must not print"
        with self.assertRaises(ValueError):
            normalize(self.response, 2000)
        self.response["cron"]["value"].pop("private")
        self.response["placement"]["value"] = {}
        with self.assertRaises(ValueError):
            normalize(self.response, 2000)

    def test_uses_account_token_and_encodes_selected_profile(self):
        args = argparse.Namespace(profile_name="name/one", json=True)
        with patch("hermes_cli.config.load_env", return_value={"MITHRIL_ACCOUNT_TOKEN": "synthetic"}), \
             patch("hermes_cli.mithril_auth.request", return_value=self.response) as request, \
             patch("hermes_cli.cloud_activity.time.time", return_value=2), \
             patch("builtins.print") as output:
            show(args)
        request.assert_called_once_with("/v1/agent/profiles/name%2Fone/activity", token="synthetic")
        self.assertNotIn("synthetic", str(output.call_args))


if __name__ == "__main__":
    unittest.main()
