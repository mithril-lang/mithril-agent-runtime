"""Private per-profile container control and encrypted SQLite checkpointing.

The pinned image supplies the agent and its dependency environment. The reviewed
overlay adds placement admission before the image's agent or scheduler starts.
"""
import asyncio
import base64
from contextlib import closing
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

sys.path.insert(0, "/opt/hermes")
try:
    import hermes_yaml as yaml
except ImportError:  # The pinned older trial image predates the YAML adapter.
    import yaml
from hermes_cli import profile_placement as placement

# Each VM is one host. Its assigned profile must be that host's default root,
# outside the shared-host profiles/ catalog (which would select an empty parent).
HOME = Path("/opt/data/placed") / os.environ["PILOT_PROFILE"]
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
    prefixes.append("/opt/data/profiles/" + HOME.name)  # Upgrade the initial pilot checkpoint.
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
    config.setdefault("gateway", {}).pop("multiplex_profiles", None)
    config.setdefault("platforms", {})["api_server"] = {"enabled": True}
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    (HOME / "workspace").mkdir(exist_ok=True)
    return {"rewritten_files": changed, "mac_mcp_servers_not_started": disabled}

# Exact pilot jobs captured at handoff. A smoke job or another same-named job
# must never be mistaken for the original business cron.
BUSINESS_JOBS = {
    "mithril-abb-information-bounty": "e01760b1c7a3",
    "mithril-accxia-bounty": "7c661c726ee0",
    "mithril-adevinta-bounty": "af12a07fbe87",
    "mithril-aelbox-bounty": "2d7dcd0e9d9c",
    "mithril-agilepulse-bounty": "e74c23855ce1",
    "mithril-airtable-bounty": "06e48bb11e47",
    "mithril-akeles-bounty": "4cd06125b1a1",
    "mithril-alohi-bounty": "a3896b32e64a",
    "mithril-alsco-bounty": "7f96dffaa717",
    "mithril-amoeboids-bounty": "52fb13b85d82",
}


def _activity_time(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        result = int(parsed.timestamp() * 1000)
        return result if 0 <= result <= 253402300799999 else None
    except (ValueError, OverflowError, OSError):
        return None


def business_activity(name, jobs, observed_at):
    """Minimized observation; no names, prompts, errors or execution output."""
    job_id = BUSINESS_JOBS.get(name)
    if not job_id or not isinstance(jobs, list):
        return {"state": "unavailable"}
    matches = [job for job in jobs if isinstance(job, dict) and job.get("id") == job_id]
    if len(matches) != 1 or not isinstance(observed_at, int) or observed_at < 0:
        return {"state": "unavailable"}
    job = matches[0]
    last = _activity_time(job.get("last_run_at"))
    status = job.get("last_status")
    # An undated success or failure is not evidence of a completed run.
    result = status if status in ("ok", "error") and last is not None else "unknown"
    return {"state": "observed", "observedAt": observed_at, "value": {
        "result": result,
        "lastRunAt": last,
        "nextRunAt": _activity_time(job.get("next_run_at")) if job.get("enabled") is True else None,
    }}


class Runtime:
    def __init__(self):
        self.child = None
        self.lock = asyncio.Lock()
        self.start_lock = asyncio.Lock()
        self.draining = False
        self.gateway_ready = False
        self.boot_id = secrets.token_hex(16)
        self.checkpoint_digest = None
        self.started = time.time()
        self.adaptation = None
        self.start_failure = None

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
        if (HOME / "config.yaml").exists():
            self.adaptation = adapt_paths()
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
        async with self.start_lock:
            await self.start_impl()

    async def start_impl(self):
        if self.draining:
            raise placement.PlacementError("Runtime is draining for a controlled restart")
        if placement.status(HOME)["phase"] != "active":
            return
        if self.child is not None and self.child.returncode is None and self.gateway_ready:
            return
        if self.child is None or self.child.returncode is not None:
            self.gateway_ready = False
            with placement.execution(HOME):
                with (HOME / "pilot-gateway.log").open("ab", buffering=0) as log:
                    self.child = await asyncio.create_subprocess_exec(
                        "/opt/hermes/scripts/mithril", "gateway", "run", "--no-supervise", "--external-supervisor",
                        cwd=HOME / "workspace", env=self.child_env(), stdout=log, stderr=log)
        async with ClientSession(timeout=ClientTimeout(total=3)) as client:
            deadline = asyncio.get_running_loop().time() + 180
            for _ in range(180):
                if self.draining:
                    raise placement.PlacementError("Gateway startup is draining for a controlled restart")
                if asyncio.get_running_loop().time() >= deadline:
                    break
                if self.child.returncode is not None:
                    raise RuntimeError("Profile gateway exited before readiness")
                try:
                    async with client.get("http://127.0.0.1:7861/health") as response:
                        if response.status == 200:
                            self.gateway_ready = True
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
        history = {}
        if (HOME / "state.db").exists():
            with closing(sqlite3.connect(f"file:{HOME / 'state.db'}?mode=ro", uri=True, timeout=3)) as db:
                history["integrity"] = db.execute("PRAGMA quick_check").fetchone()[0]
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table in ("sessions", "messages"):
                    if table in tables:
                        history[table] = db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
        process_rss = {}
        for label, pid in [("controller", os.getpid()), ("gateway", self.child.pid if self.child is not None and self.child.returncode is None else None)]:
            if pid is not None:
                for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                    if line.startswith("VmRSS:"):
                        process_rss[label + "_rss_kib"] = int(line.split()[1])
        return {"profile": HOME.name, "placement": record, "location": placement.location(), "checkpoint": self.checkpoint_digest,
                "boot_id": self.boot_id, "draining": self.draining, "gateway_ready": self.gateway_ready,
                "gateway_pid": self.child.pid if self.child is not None and self.child.returncode is None else None,
                "gateway_exit_code": self.child.returncode if self.child is not None else None, "start_failure": self.start_failure,
                "jobs": jobs, "next_due_ms": min(next_due) if next_due and record["phase"] == "active" else None,
                "uptime_seconds": round(time.time() - self.started, 2), "controller_maxrss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "overlay": json.loads(Path("/tmp/pilot-overlay.json").read_text()), "adaptation": self.adaptation, "history": history, "process_memory": process_rss}

    async def handle(self, request):
        if request.path == "/health":
            return web.json_response({"ready": True})
        if not secrets.compare_digest(request.headers.get("Authorization", ""), "Bearer " + TOKEN):
            return web.Response(status=401)
        preparing_restart = request.path == "/prepare-restart" and request.method == "POST"
        if preparing_restart:
            self.draining = True
        elif self.draining and request.method != "GET":
            return web.json_response({"error": "Runtime is draining"}, status=409)
        async with self.lock:
            try:
                if self.draining and not preparing_restart and request.method != "GET":
                    return web.json_response({"error": "Runtime is draining"}, status=409)
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
                if preparing_restart:
                    if self.child is not None and self.child.returncode is None:
                        self.child.send_signal(signal.SIGTERM)
                        try:
                            await asyncio.wait_for(asyncio.shield(self.child.wait()), 300)
                        except asyncio.TimeoutError:
                            return web.json_response({"error": "Admitted work did not drain; container preserved"}, status=503)
                    self.gateway_ready = False
                    return web.json_response({"checkpoint": await self.checkpoint(), "boot_id": self.boot_id, "drained": True})
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
                    marker = HOME / "workspace/pilot-cron-marker.txt"
                    if marker.exists() and marker.read_text() == nonce:
                        return web.json_response({"completed": True, "job_id": None})
                    # Use the actual public cron store and ordinary gateway ticker.
                    code = "from pathlib import Path; import json; from cron.jobs import create_job,load_jobs; "
                    code += "name=" + repr("placement-smoke:" + nonce) + "; "
                    code += "script=Path(" + repr(str(HOME / "scripts/pilot-smoke.py")) + "); script.parent.mkdir(exist_ok=True); "
                    script = "from pathlib import Path\nPath(" + repr(str(HOME / "workspace/pilot-cron-marker.txt")) + ").write_text(" + repr(nonce) + ")\nprint(" + repr(nonce) + ")\n"
                    code += "script.write_text(" + repr(script) + "); rows=load_jobs(); existing=next((j for j in rows if j.get('name')==name),None); "
                    due = (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat()
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
                if request.path == "/cron-run":
                    await self.start()
                    job_id = body["job_id"]
                    if job_id not in {j["id"] for j in self.state()["jobs"]}:
                        return web.Response(status=404)
                    with placement.execution(HOME):
                        child = await asyncio.create_subprocess_exec("/opt/hermes/scripts/mithril", "cron", "run", job_id, env=self.child_env(), cwd=HOME / "workspace", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                        await child.wait()
                    await self.checkpoint()
                    job = next(j for j in self.state()["jobs"] if j["id"] == job_id)
                    return web.json_response({"cli_exit_code": child.returncode, "job": job, "checkpoint": self.checkpoint_digest})
                if request.path == "/tick":
                    await self.start()
                    with placement.execution(HOME):
                        child = await asyncio.create_subprocess_exec(PYTHON, "-c", "from cron.scheduler import tick; tick(verbose=False,sync=True)", env=self.child_env(), cwd="/opt/hermes", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                        await child.wait()
                    await self.checkpoint()
                    # Reporting must not turn an acknowledged tick into an
                    # alarm retry if the diagnostic read fails.
                    try:
                        activity = business_activity(HOME.name, self.state()["jobs"], int(time.time() * 1000))
                    except Exception:
                        activity = {"state": "unavailable"}
                    return web.json_response({"exit_code": child.returncode, "business_activity": activity})
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
        try:
            await runtime.start()
        except Exception as error:
            # Preserve the authenticated control plane and startup log when a
            # restored child cannot start. Never change placement or fall back.
            runtime.start_failure = type(error).__name__
            print("Restored gateway startup failed:", runtime.start_failure, flush=True)
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
