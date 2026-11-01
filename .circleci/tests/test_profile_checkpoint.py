"""Exercise the real encrypted checkpoint protocol over a local HTTP store."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hermes_cli import profile_placement as placement


class CheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_encrypted_checkpoint_readback_and_restart_restore(self):
        key = secrets.token_hex(32)
        with patch.dict(os.environ, {"PILOT_PROFILE": "test-profile", "PILOT_CONTROL_KEY": "test-only-token", "PILOT_TRANSFER_KEY": key, "PILOT_ORIGIN": "http://127.0.0.1"}):
            spec = importlib.util.spec_from_file_location("pilot_runtime", ROOT / "cloud/profile-pilot/runtime.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        stored = {}
        async def store(request):
            if request.headers.get("Authorization") != "Bearer test-only-token":
                return web.Response(status=401)
            if request.method == "PUT":
                body = await request.read()
                digest = hashlib.sha256(body).hexdigest()
                if request.headers.get("X-Checkpoint-SHA256") != digest:
                    return web.Response(status=400)
                stored.update(body=body, digest=digest)
                return web.json_response({"sha256": digest})
            return web.Response(body=stored["body"], headers={"X-Checkpoint-SHA256": stored["digest"]})
        app = web.Application()
        app.router.add_route("*", "/store", store)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            with tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary) / "profiles/test-profile"
                home.mkdir(parents=True)
                (home / "profile.yaml").write_text("name: test-profile")
                (home / ".env").write_text("TEST_SECRET=private-checkpoint-marker")
                (home / "cron").mkdir()
                job = {"id": "same-job", "enabled": True, "schedule": {"kind": "cron", "expr": "* * * * *"}}
                (home / "cron/jobs.json").write_text(json.dumps({"jobs": [job]}))
                placement.enroll(home)
                connection = sqlite3.connect(home / "state.db")
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE history(body TEXT)")
                connection.execute("INSERT INTO history VALUES ('restart-history-marker')")
                connection.commit()
                module.HOME = home
                module.STORE = f"http://127.0.0.1:{port}/store"
                runtime = module.Runtime()
                await runtime.checkpoint()
                connection.close()
                self.assertNotIn(b"private-checkpoint-marker", stored["body"])
                self.assertNotIn(b"restart-history-marker", stored["body"])
                prior = placement.status(home)
                shutil.move(home, home.with_name("retained-source"))
                recovered = module.Runtime()
                await recovered.restore()
                self.assertEqual(placement.status(home), prior)
                self.assertEqual(json.loads((home / "cron/jobs.json").read_text())["jobs"], [job])
                with sqlite3.connect(home / "state.db") as db:
                    self.assertEqual(db.execute("PRAGMA integrity_check").fetchone(), ("ok",))
                    self.assertEqual(db.execute("SELECT body FROM history").fetchone(), ("restart-history-marker",))
                stored["body"] = stored["body"][:-1] + bytes([stored["body"][-1] ^ 1])
                with self.assertRaises(RuntimeError):
                    await recovered.restore()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
