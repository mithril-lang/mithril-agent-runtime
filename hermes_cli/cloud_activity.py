"""Read-only, account-scoped Cloud Agent activity for a selected profile."""
from __future__ import annotations

import datetime
import json
import time
from urllib.parse import quote

MAX_AGE_MS = 15 * 60 * 1000
MAX_TS = 8640000000000000


def _timestamp(value):
    return type(value) is int and 0 <= value <= MAX_TS


def _observation(value, allowed, now):
    if not isinstance(value, dict):
        raise ValueError("Invalid activity response")
    if value == {"state": "unavailable"}:
        return "unavailable"
    if set(value) != {"state", "value", "observedAt"} or value["state"] != "observed" or not _timestamp(value["observedAt"]):
        raise ValueError("Invalid activity response")
    if value["value"] not in allowed:
        raise ValueError("Invalid activity response")
    if value["observedAt"] > now or now - value["observedAt"] > MAX_AGE_MS:
        return "unknown"
    return value["value"]


def normalize(value, now):
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "placement", "container", "alarm", "cron", "checkpoint"} or value["schemaVersion"] != 1:
        raise ValueError("Invalid activity response")
    result = {
        "placement": _observation(value["placement"], {"local", "cloud", "unknown"}, now),
        "container": _observation(value["container"], {"running", "inactive", "failed", "unknown"}, now),
        "alarm": _observation(value["alarm"], {"ok", "error", "unknown"}, now),
        "checkpoint": _observation(value["checkpoint"], {"verified", "unverified", "unknown"}, now),
    }
    cron = value["cron"]
    if not isinstance(cron, dict):
        raise ValueError("Invalid activity response")
    if cron == {"state": "unavailable"}:
        result["cron"] = {"result": "unavailable", "lastRunAt": None, "nextRunAt": None}
        return result
    if set(cron) != {"state", "observedAt", "value"} or cron["state"] != "observed" or not _timestamp(cron["observedAt"]):
        raise ValueError("Invalid activity response")
    job = cron["value"]
    if not isinstance(job, dict) or set(job) != {"result", "lastRunAt", "nextRunAt"} or job["result"] not in {"ok", "error", "running", "unknown"} or any(job[k] is not None and not _timestamp(job[k]) for k in ("lastRunAt", "nextRunAt")):
        raise ValueError("Invalid activity response")
    if cron["observedAt"] > now or now - cron["observedAt"] > MAX_AGE_MS:
        result["cron"] = {"result": "unknown", "lastRunAt": None, "nextRunAt": None}
    else:
        result["cron"] = dict(job)
    return result


def show(args):
    from hermes_cli.config import load_env
    from hermes_cli.mithril_auth import TOKEN_ENV, SignInError, request

    profile = args.profile_name
    if not profile or len(profile) > 64 or any(ord(char) < 32 for char in profile):
        raise ValueError("Invalid profile name")
    token = load_env().get(TOKEN_ENV)
    if not token:
        raise SignInError("Not signed in. Run mithril login.")
    raw = request("/v1/agent/profiles/" + quote(profile, safe="") + "/activity", token=token)
    activity = normalize(raw, int(time.time() * 1000))
    if args.json:
        print(json.dumps(activity, sort_keys=True))
        return
    for label, key in (("Placement", "placement"), ("Container", "container"), ("Alarm", "alarm"), ("Business cron", "cron"), ("Checkpoint", "checkpoint")):
        value = activity[key]
        print(f"{label}: {value['result'] if key == 'cron' else value}")
    for label, key in (("Last run", "lastRunAt"), ("Next run", "nextRunAt")):
        timestamp = activity["cron"][key]
        try:
            text = datetime.datetime.fromtimestamp(timestamp / 1000, datetime.timezone.utc).isoformat() if timestamp is not None else "unknown"
        except (OverflowError, OSError, ValueError):
            text = "unknown"
        print(f"{label}: {text}")
