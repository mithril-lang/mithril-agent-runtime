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
    if not isinstance(value["value"], str) or value["value"] not in allowed:
        raise ValueError("Invalid activity response")
    if value["observedAt"] > now or now - value["observedAt"] > MAX_AGE_MS:
        return "unknown"
    return value["value"]


def normalize(value, now):
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "placement", "container", "alarm", "cron", "checkpoint"} or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
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
    if not isinstance(job, dict) or set(job) != {"result", "lastRunAt", "nextRunAt"} or not isinstance(job["result"], str) or job["result"] not in {"ok", "error", "running", "unknown"} or any(job[k] is not None and not _timestamp(job[k]) for k in ("lastRunAt", "nextRunAt")):
        raise ValueError("Invalid activity response")
    if cron["observedAt"] > now or now - cron["observedAt"] > MAX_AGE_MS:
        result["cron"] = {"result": "unknown", "lastRunAt": None, "nextRunAt": None}
        # Preserve only dated completed history, never an old running state.
        if (cron["observedAt"] <= now and job["result"] in {"ok", "error"}
                and job["lastRunAt"] is not None and job["lastRunAt"] <= cron["observedAt"]):
            result["cron"]["lastObserved"] = {"result": job["result"],
                "lastRunAt": job["lastRunAt"], "observedAt": cron["observedAt"]}
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
    historical = activity["cron"].get("lastObserved")
    if historical:
        try:
            last = datetime.datetime.fromtimestamp(historical["lastRunAt"] / 1000, datetime.timezone.utc).isoformat()
            observed = datetime.datetime.fromtimestamp(historical["observedAt"] / 1000, datetime.timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            last = observed = "unknown"
        print(f"Last observed business cron result: {historical['result']} (stale; last run {last}; observed {observed})")


# The operator key remains in the operator's private environment and is never
# sent to a caller-provided URL or exposed in output.
_PILOT_ORIGIN = "https://mithril-profile-pilot-20261101.cloud-kotoba.workers.dev"
_PILOT_NAMES = frozenset({
    "mithril-abb-information-bounty", "mithril-airtable-bounty", "mithril-akeles-bounty",
    "mithril-alohi-bounty", "mithril-amoeboids-bounty", "mithril-agilepulse-bounty",
    "mithril-aelbox-bounty", "mithril-alsco-bounty", "mithril-accxia-bounty", "mithril-adevinta-bounty",
})


def approve_pairing(args):
    """Confirm one digest from stdin; only the pilot operator may run this."""
    import getpass
    import os
    import re
    import sys
    import urllib.request
    from urllib.error import HTTPError, URLError

    name, expiry = args.profile_name, args.expires_at
    key = os.environ.get("MITHRIL_PILOT_SERVICE_KEY", "")
    now = int(time.time() * 1000)
    if name not in _PILOT_NAMES or not key or type(expiry) is not int or not now < expiry <= now + 300000:
        raise ValueError("Approval unavailable")
    digest = getpass.getpass("Pairing digest: ") if sys.stdin.isatty() else sys.stdin.readline(128).strip()
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Approval unavailable")
    payload = json.dumps({"digest": digest, "expiresAt": expiry}, separators=(",", ":")).encode()
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    request = urllib.request.Request(_PILOT_ORIGIN + "/profiles/" + name + "/activity-approve",
        data=payload, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
            if response.status != 204:
                raise ValueError("Approval unavailable")
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        raise ValueError("Approval unavailable") from None
    print("Pairing approved. Return to the signed-in app to complete it.")
