"""Verify Bot projection follows the real Profile-owned SQLite terminal fence."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from cron import executions
from hermes_cli import posthog_analytics as analytics

class LedgerProjection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ledger_projects_only_the_winning_terminal_transition(self):
        with patch.object(executions,"EXECUTIONS_FILE",self.home/"executions.db"), \
             patch.object(executions,"_emit_execution_state"), \
             patch.object(executions,"record_cron_finish"), \
             patch.object(analytics,"queue_bot_execution") as queue:
            attempt=executions.create_execution("fixture",source="scheduled")
            self.assertIsNotNone(executions.mark_execution_running(attempt["id"]))
            self.assertIsNotNone(executions.finish_execution(
                attempt["id"],success=True,delivery_outcome="queued"))
            self.assertIsNone(executions.finish_execution(
                attempt["id"],success=True,delivery_outcome="delivered"))
            self.assertEqual(1,queue.call_count)
            self.assertEqual("queued",queue.call_args.args[0]["delivery_outcome"])


if __name__ == "__main__":
    unittest.main()
