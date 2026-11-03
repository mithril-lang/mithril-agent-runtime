"""Optional, Profile-scoped Mithril usage metadata. Never exports command arguments or content."""
from __future__ import annotations
import argparse
import contextvars
from concurrent.futures import ThreadPoolExecutor
import threading
import json
import os
from pathlib import Path
import re
import time
import urllib.request
import uuid

POLICY = "mithril-posthog-v2"
ENDPOINT = "https://api.mithril.fund/v1/analytics/events"
CHANNELS = ("product", "bot", "crm", "campaign", "x")
OUTCOMES = ("started", "completed", "failed", "held", "unknown", "viewed", "clicked")
FEATURES = ("app", "navigation", "workspace", "settings", "chat", "code", "voice", "profile", "schedule", "campaign", "crm", "content", "command")

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("Analytics redirects are refused")

def consent_path(home=None):
    if home is None:
        from hermes_constants import get_hermes_home
        home = get_hermes_home()
    root = Path(home)
    if not root.is_dir():
        raise ValueError("The active Profile home is unavailable")
    path = root / ".posthog-consent.json"
    if path.is_symlink():
        raise ValueError("Analytics consent must be a Profile-owned regular file")
    return path

def read_consent(home=None):
    try:
        path = consent_path(home)
        if not path.is_file() or path.stat().st_size > 4096:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("policy") != POLICY or value.get("enabled") is not True:
            return None
        if str(uuid.UUID(value.get("profileRef", ""))) != value["profileRef"]:
            return None
        return value
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None

def set_consent(enabled, home=None):
    path = consent_path(home)
    prior = read_consent(home)
    value = {"policy":POLICY, "enabled":enabled is True, "region":"US",
             "profileRef":prior["profileRef"] if prior else str(uuid.uuid4()),
             "changedAt":int(time.time())}
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        os.chmod(path, 0o600)
        output.write(json.dumps(value, sort_keys=True)+"\n")
        output.flush(); os.fsync(output.fileno())
    return value

def record(channel, outcome, feature, *, campaign_ref=None, evidence_digest=None,
           home=None, token_reader=None, opener=None, client="cli"):
    # No token lookup, queue, disk event or network request without the active Profile choice.
    consent = read_consent(home)
    if not consent or os.environ.get("MITHRIL_AGENT") != "1":
        return {"state":"disabled", "indexingVerified":False}
    if client not in ("cli", "bot"):
        raise ValueError("Unknown analytics client")
    if channel not in CHANNELS or outcome not in OUTCOMES or feature not in FEATURES:
        raise ValueError("Unknown analytics dimension")
    if campaign_ref is not None and not re.fullmatch(r"[a-f0-9]{64}", campaign_ref):
        raise ValueError("Campaign reference must be an opaque SHA-256")
    if evidence_digest is not None and not re.fullmatch(r"[a-f0-9]{64}", evidence_digest):
        raise ValueError("Evidence digest must be SHA-256")
    if token_reader is None:
        from agent.secret_scope import serves_routed_profile
        if serves_routed_profile():
            return {"state":"profile-consent-required", "indexingVerified":False}
    if token_reader is None:
        from hermes_cli.mithril_account import read_mithril_account_token
        token_reader = read_mithril_account_token
    token = token_reader()
    if not isinstance(token, str) or not token or len(token)>4096 or re.search(r"\s",token):
        return {"state":"credential-required", "indexingVerified":False}
    event = {"id":str(uuid.uuid4()), "client":client, "channel":channel, "outcome":outcome,
             "feature":feature, "at":int(time.time()), "profileRef":consent["profileRef"]}
    if campaign_ref: event["campaignRef"]=campaign_ref
    if evidence_digest: event["evidenceDigest"]=evidence_digest
    payload=json.dumps({"policy":POLICY,"consented":True,"event":event},separators=(",",":")).encode()
    request=urllib.request.Request(ENDPOINT,data=payload,method="POST",
        headers={"Authorization":"Bearer "+token,"Content-Type":"application/json",
                 "User-Agent":"MithrilCLI/0.1 (analytics metadata)","Accept":"application/json"})
    result={"state":"unknown","eventId":event["id"],"indexingVerified":False}
    # One bounded attempt. A timeout is unknown, never retried or counted as a conversion.
    try:
        with (opener or urllib.request.build_opener(NoRedirect())).open(request,timeout=2) as response:
            raw=response.read(8193)
            if response.status==202 and len(raw)<=8192:
                value=json.loads(raw)
                if value.get("eventId")==event["id"] and value.get("state")=="accepted":
                    result["state"]="accepted"
    except Exception:
        pass
    return result

# The best-effort projection never delays cron delivery or persists an event for retry.
_BOT_LIMIT = threading.BoundedSemaphore(64)
_BOT_LOCK = threading.Lock()
_BOT_EXECUTOR = None

def queue_bot_execution(row):
    """Queue one coarse execution result after a winning durable terminal write.

    Delivery is deliberately not inferred: queued/suppressed/failed delivery can
    coexist with a completed execution. Unknown or unstarted attempts are excluded.
    """
    if not isinstance(row, dict) or not row.get("started_at") or row.get("status") not in ("completed", "failed"):
        return False
    if row.get("delivery_outcome") not in ("delivered", "queued", "failed", "not_configured", "suppressed", "suppressed_acked"):
        return False
    try:
        from hermes_constants import get_hermes_home
        home = Path(get_hermes_home())
        if os.environ.get("MITHRIL_AGENT") != "1" or not read_consent(home):
            return False
        if not _BOT_LIMIT.acquire(blocking=False):
            return False
        try:
            context = contextvars.copy_context()
            outcome = row["status"]
            def send():
                try:
                    context.run(record, "bot", outcome, "schedule", home=home, client="bot")
                except Exception:
                    pass
                finally:
                    _BOT_LIMIT.release()
            global _BOT_EXECUTOR
            with _BOT_LOCK:
                if _BOT_EXECUTOR is None:
                    _BOT_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mithril-bot-analytics")
                _BOT_EXECUTOR.submit(send)
            return True
        except Exception:
            _BOT_LIMIT.release()
            return False
    except Exception:
        return False


def reported_command(callback, args):
    try:
        result=callback(args)
    except SystemExit as error:
        _command_receipt("completed" if error.code is None or error.code == 0 else "failed")
        raise
    except KeyboardInterrupt:
        _command_receipt("held")
        raise
    except BaseException:
        _command_receipt("failed")
        raise
    _command_receipt("failed" if isinstance(result,int) and result != 0 else "completed")
    return result

def _command_receipt(outcome):
    try:
        record("product",outcome,"command")
    except Exception:
        pass  # Optional analytics cannot change a command's exit code or disclose its exception.

def cmd_analytics(args):
    action=args.analytics_action or "status"
    if action=="enable":
        set_consent(True)
        print("Optional Mithril usage metadata enabled for this Profile (PostHog US). No prompts, arguments, contacts or files are sent. CLI Session Replay is not applicable.")
    elif action=="disable":
        set_consent(False)
        print("Future Mithril analytics disabled for this Profile. Previously received data follows the Privacy Policy.")
    elif action=="record":
        result=record(args.channel,args.outcome,args.feature,campaign_ref=args.campaign_ref,evidence_digest=args.evidence_digest)
        print(json.dumps(result))
        return 0 if result["state"]=="accepted" else 1
    else:
        print(json.dumps({"policy":POLICY,"enabled":read_consent() is not None,"region":"US",
          "destination":ENDPOINT,"replay":"not-applicable","privacy":"https://mithril.fund/legal/privacy/"}))

def build_analytics_parser(subparsers):
    parser=subparsers.add_parser("analytics",help="Choose optional Mithril usage metadata for the active Profile")
    parser.set_defaults(func=cmd_analytics)
    actions=parser.add_subparsers(dest="analytics_action")
    actions.add_parser("status",help="Show the current Profile choice without contacting PostHog")
    enable=actions.add_parser("enable",help="Opt into coarse usage metadata sent to PostHog US through Mithril")
    enable.add_argument("--usage-consent",action="store_true",required=True,
                        help="Confirm optional US analytics; excludes prompts, arguments, contact records and files")
    actions.add_parser("disable",help="Stop future optional analytics for this Profile")
    send=actions.add_parser("record",help="Explicitly report a verified channel outcome; never sends a campaign")
    send.add_argument("--channel",choices=CHANNELS,required=True)
    send.add_argument("--outcome",choices=OUTCOMES,required=True)
    send.add_argument("--feature",choices=FEATURES,required=True)
    send.add_argument("--campaign-ref")
    send.add_argument("--evidence-digest")
