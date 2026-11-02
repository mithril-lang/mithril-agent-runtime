import importlib.util
from pathlib import Path
import secrets
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hermes_cli import profile_placement as placement
spec = importlib.util.spec_from_file_location("pilot_installer", ROOT / "cloud/profile-pilot/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class OverlayTests(unittest.TestCase):
    def test_injected_entrypoint_refuses_nonowner_before_script_body(self):
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "profiles/owned"
            home.mkdir(parents=True)
            (home / "profile.yaml").write_text("name: owned\n")
            with patch.object(placement, "location", return_value="host:source"):
                placement.enroll(home)
                placement.freeze(home, secrets.token_hex(16), "container:destination")
            target = root / "scheduler.py"
            target.write_text('"""Fixture runtime entrypoint."""\nfrom __future__ import annotations\nimport os\ndef run_one_job():\n    raise AssertionError("script must not execute")\n')
            installer.inject(root, ["scheduler.py"], "run_one_job", "guard_job")
            module_spec = importlib.util.spec_from_file_location("fixture_runtime", target)
            module = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(module)
            token = set_hermes_home_override(str(home))
            try:
                with self.assertRaises(placement.PlacementError):
                    module.run_one_job()
            finally:
                reset_hermes_home_override(token)

    def test_unknown_image_shape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scheduler.py").write_text("import os\ndef unrelated(): pass\n")
            with self.assertRaises(ValueError):
                installer.inject(root, ["scheduler.py"], "run_one_job", "guard_job")


class BusinessActivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = {"PILOT_PROFILE": "mithril-abb-information-bounty", "PILOT_CONTROL_KEY": "test",
               "PILOT_TRANSFER_KEY": "00" * 32, "PILOT_ORIGIN": "https://example.invalid"}
        with patch.dict("os.environ", env), patch.object(placement, "location", return_value="container:test"):
            module_spec = importlib.util.spec_from_file_location("pilot_runtime_activity", ROOT / "cloud/profile-pilot/runtime.py")
            cls.runtime = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(cls.runtime)

    def test_selects_exact_original_job_and_redacts_private_fields(self):
        job = {"id": "e01760b1c7a3", "enabled": True, "last_status": "ok",
               "last_run_at": "2026-11-02T18:09:26+09:00", "next_run_at": "2026-11-03T00:07:00+09:00",
               "name": "private job", "prompt": "private prompt", "last_error": "private output"}
        result = self.runtime.business_activity("mithril-abb-information-bounty", [job], 1793620000000)
        self.assertEqual(result["state"], "observed")
        self.assertEqual(result["value"]["result"], "ok")
        self.assertEqual(set(result["value"]), {"result", "lastRunAt", "nextRunAt"})
        self.assertNotIn("private", str(result))

    def test_missing_duplicate_wrong_profile_and_undated_results_fail_closed(self):
        job = {"id": "e01760b1c7a3", "enabled": True, "last_status": "ok",
               "last_run_at": "2026-11-02T18:09:26", "next_run_at": None}
        self.assertEqual(self.runtime.business_activity("unknown", [job], 1)["state"], "unavailable")
        self.assertEqual(self.runtime.business_activity("mithril-abb-information-bounty", [], 1)["state"], "unavailable")
        self.assertEqual(self.runtime.business_activity("mithril-abb-information-bounty", [job, job], 1)["state"], "unavailable")
        result = self.runtime.business_activity("mithril-abb-information-bounty", [job], 1)
        self.assertEqual(result["value"]["result"], "unknown")
        self.assertIsNone(result["value"]["lastRunAt"])

    def test_disabled_job_has_no_next_due_and_errors_stay_distinct(self):
        job = {"id": "e01760b1c7a3", "enabled": False, "last_status": "error",
               "last_run_at": "2026-11-02T18:09:26+09:00",
               "next_run_at": "2026-11-03T00:07:00+09:00"}
        value = self.runtime.business_activity("mithril-abb-information-bounty", [job], 1)["value"]
        self.assertEqual(value["result"], "error")
        self.assertIsNone(value["nextRunAt"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
