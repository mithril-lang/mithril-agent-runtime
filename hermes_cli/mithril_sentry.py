"""Content-free error envelopes for the Mithril-branded CLI boundary only."""
import json
import os
import re
import sys
import threading
import time
import urllib.request
import uuid
from functools import lru_cache
from pathlib import Path

TUNNEL = "https://api.mithril.fund/sentry/tunnel"
_window = 0.0
_seen = []
_installed = False
_lock = threading.Lock()
_types = {"Exception", "RuntimeError", "ValueError", "TypeError", "KeyError", "OSError", "ImportError", "ModuleNotFoundError", "AttributeError", "AssertionError", "TimeoutError"}


@lru_cache(maxsize=4)
def reporting_revision(root=None):
    """Bind provenance to this module's package, never an active profile/runtime."""
    from hermes_cli.steward import read_install_stamp
    from hermes_cli.version_info import _git_version_info
    root = root or Path(__file__).resolve().parent.parent
    stamp = read_install_stamp(root)
    revision = stamp.get("commit")
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}", revision) or set(revision) == {"0"}:
        info = _git_version_info(root)
        revision = info.commit if info else None
    return revision if isinstance(revision, str) and re.fullmatch(r"[a-f0-9]{40}", revision) else "unknown"


def reporting_enabled(config=None):
    """Read the active profile's explicit consent; malformed/unreadable settings stay off."""
    try:
        if config is None:
            from hermes_cli.config import load_config_readonly
            config = load_config_readonly()
        telemetry = config.get("telemetry") if isinstance(config, dict) else None
        reports = telemetry.get("error_reports") if isinstance(telemetry, dict) else None
        return isinstance(reports, dict) and reports.get("enabled") is True
    except Exception:
        return False


def build_event(error, tb=None, diagnostic=False, reliability=None):
    release = "mithril-cli-" + reporting_revision()
    if not re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", release):
        release = "mithril-cli-unknown"
    root = Path(__file__).resolve().parent.parent
    frames = []
    while tb is not None and len(frames) < 20:
        path = Path(tb.tb_frame.f_code.co_filename).resolve()
        if path.is_relative_to(root) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}\.py", path.name):
            frames.append({"filename": path.name, "lineno": tb.tb_lineno, "in_app": True})
        tb = tb.tb_next
    name = type(error).__name__
    event = {
        "event_id": uuid.uuid4().hex, "timestamp": time.time(), "platform": "python",
        "level": "info" if diagnostic else "error", "environment": "production", "release": release,
        "tags": {"surface": "cli", **({"diagnostic": "true"} if diagnostic else {})},
        "exception": {"values": [{"type": name if name in _types else "Exception",
                                   "value": "Mithril Sentry diagnostic" if diagnostic else "CLI error",
                                   **({"stacktrace": {"frames": frames}} if frames else {})}]},
    }

    if isinstance(reliability, dict):
        fields = {"page": {"workspace", "startup", "profile"}, "operation": {"sync", "initial", "read"},
                  "error_code": {"latency_exceeded", "operation_stalled", "operation_failed"}}
        if (all(reliability.get(key) in allowed for key, allowed in fields.items())
                and all(type(reliability.get(key)) in (int, float)
                        and 0 <= reliability[key] <= 86400000 for key in ("elapsed_ms", "budget_ms"))):
            event["tags"].update({key: reliability[key] for key in fields})
            event["contexts"] = {"performance": {key: reliability[key] for key in ("elapsed_ms", "budget_ms")}}
            event["fingerprint"] = ["client-reliability", "cli", *[reliability[key] for key in fields]]
    return event


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def capture(error, tb=None, diagnostic=False, opener=None, *, reliability=None):
    """Bounded best-effort send. No credential, DSN, prompt or arbitrary message."""
    global _window
    try:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            return None
        if not diagnostic and not reporting_enabled():
            return None
        with _lock:
            if time.monotonic() - _window > 60:
                _window = time.monotonic()
                _seen.clear()
            if any(old is error for old in _seen) or len(_seen) >= 5:
                return None
            _seen.append(error)
        event = build_event(error, tb, diagnostic, reliability)
        body = "\n".join(map(json.dumps, [{"event_id": event["event_id"]}, {"type": "event"}, event])).encode()
        request = urllib.request.Request(TUNNEL, data=body, method="POST", headers={
            "Content-Type": "application/x-sentry-envelope", "User-Agent": "Mithril-CLI-Sentry/1.0",
        })
        # No cookies, proxies from user config, redirects or authentication handlers.
        transport = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open
        with transport(request, timeout=2) as response:
            return event["event_id"] if 200 <= response.status < 300 else None
    except Exception:
        return None


def install():
    global _installed
    if _installed or os.environ.get("MITHRIL_CLIENT") != "cli":
        return
    _installed = True
    try:
        reporting_revision()
    except Exception:
        pass
    previous = sys.excepthook
    previous_thread = threading.excepthook

    def hook(kind, error, tb):
        capture(error, tb)
        previous(kind, error, tb)

    def thread_hook(args):
        capture(args.exc_value, args.exc_traceback)
        previous_thread(args)

    sys.excepthook = hook
    threading.excepthook = thread_hook


def main():
    event_id = capture(RuntimeError("diagnostic"), diagnostic=True)
    print(json.dumps({"sentryEventId": event_id}))
    return 0 if event_id else 1


if __name__ == "__main__":
    raise SystemExit(main())
