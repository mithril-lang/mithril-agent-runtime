"""Real SQLite, filesystem and cross-process contracts for scheduled placement."""
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hermes_cli import profile_placement as p


class PlacementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.a = self.root / "source/profiles/bounty-agent"
        self.b = self.root / "destination/profiles/bounty-agent"
        self.a.mkdir(parents=True)
        self.b.parent.mkdir(parents=True)
        (self.a / "profile.yaml").write_text("name: bounty-agent\n")
        (self.a / "cron").mkdir()
        self.job = {"id": "daily", "enabled": True, "schedule": {"kind": "cron", "expr": "* * * * *"}}
        (self.a / "cron/jobs.json").write_text(json.dumps({"jobs": [self.job]}))
        self.db = sqlite3.connect(self.a / "state.db")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE messages(body TEXT)")
        self.db.execute("INSERT INTO messages VALUES ('committed-WAL-marker')")
        self.db.commit()
        self.key, self.operation = secrets.token_hex(32), secrets.token_hex(16)
        self.identity = patch.object(p, "location", return_value="host:source")
        self.identity.start()
        p.enroll(self.a)

    def tearDown(self):
        self.identity.stop()
        if self.db is not None:
            self.db.close()
        self.temporary.cleanup()

    def transfer(self):
        self.assertEqual(p.freeze(self.a, self.operation, "container:destination")["phase"], "frozen")
        capsule = p.export(self.a, self.operation, self.key)
        with patch.object(p, "location", return_value="container:destination"):
            p.stage(self.b, capsule, self.key)
            with self.assertRaises(p.PlacementError):
                with p.execution(self.b): pass
        proof = p.release(self.a, self.operation, capsule["sha256"])
        with patch.object(p, "location", return_value="container:destination"):
            p.activate(self.b, self.operation, proof)
        return capsule

    def test_scheduled_profile_wal_history_and_job_survive_transfer(self):
        before = p.status(self.a)
        capsule = self.transfer()
        with sqlite3.connect(self.b / "state.db") as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            self.assertEqual(db.execute("SELECT body FROM messages").fetchone(), ("committed-WAL-marker",))
        self.assertEqual(json.loads((self.b / "cron/jobs.json").read_text())["jobs"], [self.job])
        self.assertEqual(before["identity"], p.status(self.b)["identity"])
        self.assertEqual(before["generation"] + 1, p.status(self.b)["generation"])
        with self.assertRaises(p.PlacementError):
            with p.execution(self.a): pass
        with patch.object(p, "location", return_value="container:destination"):
            with p.execution(self.b): pass
            # A completely fresh interpreter reads the fence after restart.
            code = "from pathlib import Path; from unittest.mock import patch; from hermes_cli import profile_placement as p; "
            code += "\nwith patch.object(p,'location',return_value='container:destination'):\n with p.execution(Path(__import__('sys').argv[1])): print('admitted')"
            result = subprocess.run([sys.executable, "-c", code, str(self.b)], text=True, capture_output=True, cwd=Path(__file__).resolve().parents[2])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "admitted")

    def test_drain_blocks_new_work_but_preserves_admitted_nested_work(self):
        entered, finish = threading.Event(), threading.Event()
        errors = []
        def worker():
            try:
                with p.execution(self.a):
                    entered.set()
                    finish.wait(5)
                    with p.execution(self.a): pass
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(p.freeze(self.a, self.operation, "container:destination")["phase"], "draining")
            with self.assertRaises(p.PlacementError):
                with p.execution(self.a): pass
            with self.assertRaises(p.PlacementError):
                p.export(self.a, self.operation, self.key)
        finally:
            finish.set()
            thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(p.freeze(self.a, self.operation, "container:destination")["phase"], "frozen")

    def test_copy_of_active_profile_to_other_location_is_refused(self):
        with patch.object(p, "location", return_value="container:copy"):
            with self.assertRaises(p.PlacementError):
                with p.execution(self.a): pass

    def test_retired_source_keeps_data_but_is_not_a_legacy_profile_directory(self):
        self.transfer()
        self.db.close()
        self.db = None
        retained = self.root / "retained" / self.operation
        p.retire_source(self.a, retained, quiesce=lambda: None)
        self.assertFalse(self.a.is_dir())
        self.assertTrue((retained / self.a.name / "state.db").is_file())
        with self.assertRaises(p.PlacementError):
            with p.execution(self.a): pass
        with self.assertRaises(p.PlacementError): p.enroll(self.a)
        self.assertEqual(p.status(self.a)["phase"], "moved")

    def test_release_proof_is_not_exposed_by_status_or_freeze(self):
        result = p.freeze(self.a, self.operation, "container:destination")
        self.assertNotIn("release", result)
        self.assertNotIn("release", p.status(self.a))
        capsule = p.export(self.a, self.operation, self.key)
        with patch.object(p, "location", return_value="container:destination"):
            p.stage(self.b, capsule, self.key)
            with self.assertRaises(p.PlacementError):
                p.activate(self.b, self.operation, "wrong-proof")

    def test_snapshot_change_and_conflicting_retry_fail_closed(self):
        p.freeze(self.a, self.operation, "container:destination")
        p.export(self.a, self.operation, self.key)
        (self.a / "SOUL.md").write_text("changed after freeze")
        with self.assertRaises(p.PlacementError):
            p.export(self.a, self.operation, self.key)
        with self.assertRaises(p.PlacementError):
            p.freeze(self.a, secrets.token_hex(16), "container:destination")

    def test_corrupt_record_never_becomes_unmanaged(self):
        (self.a / p.STATE).write_text("{}")
        with self.assertRaises(p.PlacementError):
            with p.execution(self.a): pass

    def test_real_agent_and_cron_entrypoints_refuse_the_former_owner(self):
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        from run_agent import AIAgent
        from cron.scheduler import run_one_job
        from cron.scheduler_tick import tick
        self.transfer()
        token = set_hermes_home_override(str(self.a))
        before = (self.a / "cron/jobs.json").read_bytes()
        try:
            with self.assertRaises(p.PlacementError): AIAgent(model="test-model")
            with self.assertRaises(p.PlacementError): run_one_job({"id": "daily", "no_agent": True})
            self.assertEqual(tick(sync=True), 0)
            self.assertEqual((self.a / "cron/jobs.json").read_bytes(), before)
        finally:
            reset_hermes_home_override(token)

    def test_script_and_tick_admission_share_the_same_fence_a_b_a(self):
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        calls = []
        @p.guard_job
        def script(): calls.append("ran")
        @p.guard_tick
        def tick(): script(); return 1
        self.transfer()
        for home, owner, accepted in [(self.a,"host:source",False),(self.b,"container:destination",True),(self.a,"host:source",False)]:
            token = set_hermes_home_override(str(home))
            try:
                with patch.object(p, "location", return_value=owner):
                    self.assertEqual(tick(), int(accepted))
                    if not accepted:
                        with self.assertRaises(p.PlacementError): script()
            finally:
                reset_hermes_home_override(token)
        self.assertEqual(calls, ["ran"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
