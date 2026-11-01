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


if __name__ == "__main__":
    unittest.main(verbosity=2)
