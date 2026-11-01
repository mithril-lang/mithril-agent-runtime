"""Private per-profile container control and encrypted SQLite checkpointing.

The pinned image supplies the agent and its dependency environment. The reviewed
overlay adds placement admission before the image's agent or scheduler starts.
"""
import asyncio
import base64
from datetime import datetime, timezone, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import resource
import secrets
import signal
import sqlite3
import sys
import tarfile
import time

from aiohttp import ClientSession, ClientTimeout, web
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import yaml

sys.path.insert(0, "/opt/hermes")
from hermes_cli import profile_placement as placement

HOME = Path("/opt/data/profiles") / os.environ["PILOT_PROFILE"]
TOKEN = os.environ["PILOT_CONTROL_KEY"]
CIPHER = AESGCM(bytes.fromhex(os.environ["PILOT_TRANSFER_KEY"]))
STORE = os.environ["PILOT_ORIGIN"] + "/_store/" + HOME.name
PYTHON = "/opt/hermes/.venv/bin/python"
AAD = ("mithril-profile-checkpoint-v1:" + HOME.name + ":" + placement.location()).encode()


def restore_archive(data):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        if sum(m.size for m in members) > placement.LIMIT or any(not m.isfile() or Path(m.name).is_absolute() or ".." in Path(m.name).parts or Path(m.name).parts[0] not in placement.FILES | placement.DIRS for m in members):
            raise ValueError("Unsafe stored profile archive")
        for member in members:
            p = HOME / member.name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(archive.extractfile(member).read())
            p.chmod(0o600)


def adapt_paths():
    """Map this profile's own workspace, retaining the source contract in its capsule."""
    prefixes = ["/Users/junkawasaki/." + alias + "/profiles/" + HOME.name for alias in ("hermes", "mithril")]
    changed = []
    for path in HOME.rglob("*"):
        if not path.is_file() or path.suffix not in {".json", ".yaml", ".yml", ".md", ".py", ".sh"}:
            continue
        if path.stat().st_size > 4 * 1024 * 1024 or path.name == placement.STATE:
            continue
        text = path.read_text()
        new = text
        for prefix in prefixes:
            new = new.replace(prefix, str(HOME))
        if new != text:
            path.write_text(new)
            changed.append(str(path.relative_to(HOME)))
    path = HOME / "config.yaml"
    config = yaml.safe_load(path.read_text()) or {}
    config.setdefault("terminal", {}).update(backend="local", cwd=str(HOME / "workspace"))
    # Mac executables are not a valid cloud capability. Preserve them in the
    # encrypted source capsule; report their absence instead of running a fallback.
    disabled = list((config.get("mcp_servers") or {}).keys())
    config["mcp_servers"] = {}
    config.setdefault("gateway", {})["multiplex_profiles"] = False
    config.setdefault("platforms", {})["api_server"] = {"enabled": True}
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    (HOME / "workspace").mkdir(exist_ok=True)
    return {"rewritten_files": changed, "mac_mcp_servers_not_started": disabled}


class Runtime:
    def __init__(self):
        self.child = None
        self.lock = asyncio.Lock()
        self.checkpoint_digest = None
        self.started = time.time()
        self.adaptation = None

    async def restore(self):
        async with ClientSession(timeout=ClientTimeout(total=90)) as client:
            async with client.get(STORE, headers={"Authorization": "Bearer " + TOKEN}) as response:
                if response.status == 404:
                    return
                if response.status != 200:
                    raise RuntimeError("Checkpoint store unavailable")
                encrypted = await response.read()
                digest = hashlib.sha256(encrypted).hexdigest()
                if digest != response.headers.get("X-Checkpoint-SHA256"):
                    raise RuntimeError("Stored checkpoint digest mismatch")
        data = json.loads(CIPHER.decrypt(encrypted[:12], encrypted[12:], AAD))
        if data["record"]["owner"] != placement.location():
            raise RuntimeError("Checkpoint belongs to a different runtime")
        HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
        restore_archive(base64.b64decode(data["archive"], validate=True))
        placement._write(HOME / placement.STATE, data["record"])
        self.checkpoint_digest = digest

    async def checkpoint(self):
        row = placement.read(HOME)
        if row is None:
            return None
        archive = await asyncio.to_thread(placement.snapshot, HOME)
        data = json.dumps({"record": row, "archive": base64.b64encode(archive).decode()}).encode()
        nonce = os.urandom(12)
        encrypted = nonce + CIPHER.encrypt(nonce, data, AAD)
        digest = hashlib.sha256(encrypted).hexdigest()
        async with ClientSession(timeout=ClientTimeout(total=90)) as client:
            async with client.put(STORE, data=encrypted, headers={"Authorization": "Bearer " + TOKEN, "X-Checkpoint-SHA256": digest}) as response:
                if response.status != 200:
                    raise RuntimeError("Checkpoint was not durably acknowledged")
            async with client.get(STORE, headers={"Authorization": "Bearer " + TOKEN}) as response:
                if response.status != 200 or hashlib.sha256(await response.read()).hexdigest() != digest:
                    raise RuntimeError("Checkpoint independent readback mismatch")
        self.checkpoint_digest = digest
        return digest

    def child_env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("HERMES_D1_", "PILOT_")) and k not in {"HERMES_STATE_DB_BACKEND"}}
        env.update(HERMES_HOME=str(HOME), API_SERVER_KEY=TOKEN, API_SERVER_HOST="127.0.0.1", API_SERVER_PORT="7861", MITHRIL_AGENT="1", PYTHONDONTWRITEBYTECODE="1")
        return env

    async def start(self):
        if placement.status(HOME)["phase"] != "active":
            return
        if self.child is not None and self.child.returncode is None:
            return
        with placement.execution(HOME):
            log = (HOME / "pilot-gateway.log").open("ab", buffering=0)
            self.child = await asyncio.create_subprocess_exec(
                "/opt/hermes/.venv/bin/hermes", "gateway", "run", "--external-supervisor",
                cwd=HOME / "workspace", env=self.child_env(), stdout=log, stderr=log)
        async with ClientSession(timeout=ClientTimeout(total=3)) as client:
            for _ in range(180):
                if self.child.returncode is not None:
                    raise RuntimeError("Profile gateway exited before readiness")
                try:
                    async with client.get("http://127.0.0.1:7861/health") as response:
                        if response.status == 200:
                            return
                except Exception:
                    pass
                await asyncio.sleep(1)
        raise RuntimeError("Profile gateway readiness timed out")

    def state(self):
        jobs = []
        path = HOME / "cron/jobs.json"
        if path.exists():
            value = json.loads(path.read_text())
            rows = value if isinstance(value, list) else value.get("jobs", [])
            jobs = [{k: j.get(k) for k in ("id", "name", "enabled", "last_run_at", "last_status", "next_run_at")} for j in rows]
        next_due = []
        for job in jobs:
            if job["enabled"] and job["next_run_at"]:
                next_due.append(int(datetime.fromisoformat(job["next_run_at"]).timestamp() * 1000))
        record = placement.status(HOME)
        return {"profile": HOME.name, "placement": record, "location": placement.location(), "checkpoint": self.checkpoint_digest,
                "gateway_pid": self.child.pid if self.child is not None and self.child.returncode is None else None,
                "jobs": jobs, "next_due_ms": min(next_due) if next_due and record["phase"] == "active" else None,
                "uptime_seconds": round(time.time() - self.started, 2), "controller_maxrss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "overlay": json.loads(Path("/tmp/pilot-overlay.json").read_text()), "adaptation": self.adaptation}

    async def handle(self, request):
        if request.path == "/health":
            return web.json_response({"ready": True})
        if not secrets.compare_digest(request.headers.get("Authorization", ""), "Bearer " + TOKEN):
            return web.Response(status=401)
        async with self.lock:
            try:
                body = await request.json() if request.method == "POST" else {}
                if request.path == "/status":
                    return web.json_response(self.state())
                if request.path == "/stage":
                    result = placement.stage(HOME, body["capsule"], os.environ["PILOT_TRANSFER_KEY"])
                    self.adaptation = adapt_paths()
                    await self.checkpoint()
                    return web.json_response({"placement": result, "adaptation": self.adaptation, "checkpoint": self.checkpoint_digest})
                if request.path == "/activate":
                    placement.activate(HOME, body["operation"], body["proof"])
                    await self.checkpoint()  # No acknowledgement until active ownership is durable.
                    await self.start()
                    return web.json_response(self.state())
                if request.path == "/checkpoint":
                    return web.json_response({"checkpoint": await self.checkpoint()})
                if request.path == "/chat":
                    await self.start()
                    with placement.execution(HOME):
                        headers = {"Authorization": "Bearer " + TOKEN, "X-Hermes-Session-Key": "placement-pilot:" + HOME.name}
                        if body.get("session_id"):
                            headers["X-Hermes-Session-Id"] = body["session_id"]
                        config = yaml.safe_load((HOME / "config.yaml").read_text())
                        payload = {"model": config["model"]["default"], "stream": False, "messages": [{"role": "user", "content": body["message"]}]}
                        async with ClientSession(timeout=ClientTimeout(total=240)) as client:
                            async with client.post("http://127.0.0.1:7861/v1/chat/completions", headers=headers, json=payload) as response:
                                if response.status != 200:
                                    return web.json_response({"error": "Gateway request failed", "status": response.status}, status=502)
                                result = await response.json()
                                session = response.headers.get("X-Hermes-Session-Id")
                        await self.checkpoint()
                        return web.json_response({"content": result["choices"][0]["message"]["content"], "session_id": session, "checkpoint": self.checkpoint_digest})
                if request.path == "/cron-smoke":
                    await self.start()
                    nonce = body["nonce"]
                    if not isinstance(nonce, str) or not nonce.isalnum() or len(nonce) > 64:
                        raise ValueError("Invalid smoke nonce")
                    # Use the actual public cron store and ordinary gateway ticker.
                    code = "from pathlib import Path; import json; from cron.jobs import create_job,load_jobs; "
                    code += "name=" + repr("placement-smoke:" + nonce) + "; "
                    code += "script=Path(" + repr(str(HOME / "scripts/pilot-smoke.py")) + "); script.parent.mkdir(exist_ok=True); "
                    script = "from pathlib import Path\nPath(" + repr(str(HOME / "workspace/pilot-cron-marker.txt")) + ").write_text(" + repr(nonce) + ")\nprint(" + repr(nonce) + ")\n"
                    code += "script.write_text(" + repr(script) + "); rows=load_jobs(); existing=next((j for j in rows if j.get('name')==name),None); "
                    due = (datetime.now(timezone.utc) + timedelta(seconds=15)).isoformat()
                    code += "job=existing or create_job(prompt='',schedule=" + repr(due) + ",name=name,script=str(script),no_agent=True,deliver='local'); print(json.dumps({'job_id':job['id']}))"
                    child = await asyncio.create_subprocess_exec(PYTHON, "-c", code, env=self.child_env(), cwd="/opt/hermes", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    stdout, stderr = await asyncio.wait_for(child.communicate(), 60)
                    if child.returncode:
                        raise RuntimeError("Cron smoke registration failed")
                    await self.checkpoint()
                    return web.json_response(json.loads(stdout.decode().splitlines()[-1]))
                if request.path == "/cron-result":
                    marker = HOME / "workspace/pilot-cron-marker.txt"
                    return web.json_response({"marker": marker.read_text() if marker.exists() else None, "state": self.state()})
                if request.path == "/tick":
                    await self.start()
                    with placement.execution(HOME):
                        child = await asyncio.create_subprocess_exec(PYTHON, "-c", "from cron.scheduler import tick; tick(verbose=False,sync=True)", env=self.child_env(), cwd="/opt/hermes", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                        await child.wait()
                    await self.checkpoint()
                    return web.json_response({"exit_code": child.returncode})
                return web.Response(status=404)
            except Exception as error:
                # Never return provider errors, archives, keys or process environment.
                print("Pilot control failure:", type(error).__name__, str(error) if isinstance(error, placement.PlacementError) else "", flush=True)
                return web.json_response({"error": type(error).__name__}, status=409 if isinstance(error, placement.PlacementError) else 503)


async def main():
    runtime = Runtime()
    HOME.parent.mkdir(parents=True, exist_ok=True)
    await runtime.restore()
    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_route("*", "/{path:.*}", runtime.handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 7860).start()
    if placement.status(HOME)["phase"] == "active":
        await runtime.start()
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
