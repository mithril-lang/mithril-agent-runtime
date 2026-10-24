"""Mithril Fund browser sign-in, composed at the Mithril launcher boundary."""

import argparse
import copy
import json
import math
import os
from pathlib import Path
import re
import runpy
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser

API = "https://api.mithril.fund"
CONSOLE = "https://console.mithril.fund/account/device"
TOKEN_ENV = "MITHRIL_ACCOUNT_TOKEN"
SCOPES = frozenset({"inference", "workspace:read"})
CONNECTOR_SCOPES = frozenset({"connectors:read", "connectors:write"})


class SignInError(Exception):
    """A safe, user-facing failure; never includes a response body or token."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(path, body=None, token=None):
    headers = {"Accept": "application/json", "Cache-Control": "no-store",
               "User-Agent": "Mithril-Agent-CLI/1.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        response = urllib.request.build_opener(NoRedirect()).open(req, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    except (OSError, ValueError):
        raise SignInError("Cannot reach Mithril. Check your connection and try again.") from None
    with response:
        raw = response.read(1048577)
        if len(raw) > 1048576:
            raise SignInError("Mithril returned an invalid response.")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise SignInError("Mithril returned an invalid response.") from None
        if not isinstance(value, dict):
            raise SignInError("Mithril returned an invalid response.")
        if response.status >= 300:
            code = value.get("error")
            if path == "/v1/device/token" and response.status == 400 and isinstance(code, str) and code in {
                "authorization_pending", "slow_down", "expired_token", "access_denied"
            }:
                return value
            if response.status == 401:
                raise SignInError("Your Mithril sign-in is no longer valid. Run mithril login.")
            raise SignInError("Mithril could not complete this request. Try again later.")
        return value


def validate_grant(grant):
    code = grant.get("user_code")
    device = grant.get("device_code")
    if not isinstance(code, str) or not re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}", code):
        raise SignInError("Mithril returned an invalid approval code.")
    if not isinstance(device, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", device):
        raise SignInError("Mithril returned an invalid approval code.")
    expected = CONSOLE + "?user_code=" + urllib.parse.quote(code)
    if grant.get("verification_uri") != CONSOLE or grant.get("verification_uri_complete") != expected:
        raise SignInError("Mithril returned an invalid approval address.")
    for key, maximum in (("expires_in", 600), ("interval", 60)):
        n = grant.get(key)
        if type(n) not in (int, float) or not math.isfinite(n) or not 1 <= n <= maximum:
            raise SignInError("Mithril returned an invalid approval interval.")
    return expected


def poll(grant, call=request, now=time.monotonic, wait=time.sleep, required_scopes=SCOPES):
    deadline = now() + grant["expires_in"]
    interval = grant["interval"]
    while now() < deadline:
        wait(min(interval, deadline - now()))
        if now() >= deadline:
            break
        result = call("/v1/device/token", {"device_code": grant["device_code"]})
        error = result.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        if error == "access_denied":
            raise SignInError("Sign-in was declined. No local settings were changed.")
        if error == "expired_token":
            break
        token = result.get("access_token")
        if (not isinstance(token, str) or not re.fullmatch(r"mf_[A-Za-z0-9_-]{16,252}", token)
                or result.get("token_type") != "Bearer"
                or not isinstance(result.get("scope"), str)
                or set(result["scope"].split()) != required_scopes):
            raise SignInError("Mithril did not confirm the requested sign-in permissions.")
        return token
    raise SignInError("The approval code expired. Run mithril login to start again.")


def select_model(catalog, requested, current):
    models = catalog.get("data", [])
    if not isinstance(models, list):
        raise SignInError("Mithril returned an invalid model catalog.")
    ids = [m["id"] for m in models if isinstance(m, dict) and isinstance(m.get("id"), str)]
    if requested:
        if requested not in ids:
            raise SignInError("That model is not in the Mithril catalog. Choose a listed model.")
        return requested
    if current in ids:
        return current
    routing = catalog.get("openRouterCatalog", {})
    defaults = routing.get("profileBaseModelIds", []) if isinstance(routing, dict) else []
    if not isinstance(defaults, list):
        defaults = []
    if len(defaults) == 1 and defaults[0] in ids:
        return defaults[0]
    if not ids or not sys.stdin.isatty():
        raise SignInError("Choose a model with mithril login --model MODEL_ID.")
    from hermes_cli.curses_ui import curses_single_select
    index = curses_single_select("Choose a Mithril model", ids)
    if index is None:
        raise SignInError("Sign-in cancelled. No local settings were changed.")
    return ids[index]


def select_home(profile=None):
    from hermes_constants import get_default_hermes_root
    from hermes_cli.profiles import resolve_profile_env
    if profile is None and Path(os.environ.get("HERMES_HOME", "")).parent.name != "profiles":
        active = get_default_hermes_root() / "active_profile"
        if active.is_file():
            profile = active.read_text(encoding="utf-8-sig").strip() or None
    if profile:
        os.environ["HERMES_HOME"] = resolve_profile_env(profile)


def verified_identity(identity, required_scopes=SCOPES):
    user = identity.get("user")
    scopes = identity.get("scopes")
    return (isinstance(user, dict) and isinstance(user.get("id"), str) and bool(user["id"])
            and isinstance(scopes, list) and all(isinstance(scope, str) for scope in scopes)
            and required_scopes <= set(scopes))


def persist(token, model):
    from hermes_cli.config import (atomic_config_write, get_config_path, get_env_path,
                                   load_env, require_env_writable,
                                   require_readable_config_before_write, save_env_value,
                                   remove_env_value)
    require_env_writable(TOKEN_ENV, "set")
    config = None
    if model is not None:
        original = require_readable_config_before_write()
        config = copy.deepcopy(original)
        providers = config.setdefault("providers", {})
        if not isinstance(providers, dict):
            raise SignInError("Repair the providers section in config.yaml before signing in.")
        # This dedicated route leaves existing provider keys and API credentials intact.
        providers["Mithril Account"] = {"name": "Mithril Account", "base_url": API + "/v1",
                                       "key_env": TOKEN_ENV}
        model_config = config.setdefault("model", {})
        if not isinstance(model_config, dict):
            model_config = config["model"] = {}
        # Pin the model route as well: the CLI otherwise inherits OPENROUTER_BASE_URL.
        model_config.update({"provider": "Mithril Account", "default": model, "base_url": API + "/v1"})
        stale_keys = ("api", "api_key", "key_env", "api_key_env", "key_cmd", "api_mode")
        if any(key in model_config for key in stale_keys):
            backup = get_config_path().parent / "backups" / ("before-mithril-login-" + uuid.uuid4().hex + ".yaml")
            backup.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            atomic_config_write(backup, original)
            os.chmod(backup, 0o600)
        for stale in stale_keys:
            model_config.pop(stale, None)
    previous = load_env().get(TOKEN_ENV)
    try:
        save_env_value(TOKEN_ENV, token)
        os.chmod(get_env_path(), 0o600)
        if config is not None:
            atomic_config_write(get_config_path(), config)
    except BaseException:
        if previous is None:
            remove_env_value(TOKEN_ENV)
        else:
            save_env_value(TOKEN_ENV, previous)
        raise


def uses_existing_provider_auth(argv):
    # Preserve upstream's explicit provider commands; only the provider-less
    # Mithril aliases belong to this module. Profile flags may occur anywhere.
    stripped = []
    args = iter(argv)
    for arg in args:
        if arg in ("-p", "--profile"):
            next(args, None)
        elif not arg.startswith("--profile="):
            stripped.append(arg)
    if stripped[:1] in (["login"], ["logout"]):
        return any(arg == "--provider" or arg.startswith("--provider=") for arg in stripped)
    return (stripped[:2] in (["auth", "status"], ["auth", "logout"])
            and len(stripped) > 2 and not stripped[2].startswith("-"))


def main(argv=None, *, continue_on_login=False, profile_selected=False):
    argv = list(sys.argv[1:] if argv is None else argv)
    if uses_existing_provider_auth(argv):
        previous = sys.argv
        try:
            sys.argv = [sys.argv[0], *argv]
            runpy.run_module("hermes_cli.main", run_name="__main__", alter_sys=True)
            return 0
        finally:
            sys.argv = previous
    start = 2 if argv[:1] in (["-p"], ["--profile"]) else 0
    if argv[start:start + 2] == ["auth", "status"]:
        argv[start:start + 2] = ["account", "status"]
    elif argv[start:start + 2] in (["auth", "login"], ["auth", "logout"]):
        argv.pop(start)
    parser = argparse.ArgumentParser(prog="mithril", description="Sign in to your Mithril Fund account.")
    parser.add_argument("command", choices=("login", "logout", "account"))
    parser.add_argument("action", nargs="?", choices=("status",))
    parser.add_argument("--model", help="Mithril model identifier to use after sign-in")
    parser.add_argument("--no-browser", action="store_true", help="Print the approval URL without opening it")
    parser.add_argument("--connectors", action="store_true", help="Authorize native connectors without changing inference settings")
    parser.add_argument("-p", "--profile", help="Existing local Profile (default: current Profile)")
    args = parser.parse_args(argv)
    try:
        # Resolve before importing config or secret readers; never share another Profile's token.
        if not profile_selected:
            select_home(args.profile)
        from hermes_cli.config import (is_managed, load_env,
                                       require_readable_config_before_write,
                                       require_env_writable, remove_env_value)
        if args.command == "logout":
            require_env_writable(TOKEN_ENV, "remove")
            remove_env_value(TOKEN_ENV)
            print("Signed out on this Profile. Manage or revoke device access at https://console.mithril.fund/account.")
            return 0
        if args.command == "account":
            token = load_env().get(TOKEN_ENV)
            if not token:
                print("Not signed in. Run mithril login.")
                return 1
            identity = request("/v1/me", token=token)
            if not verified_identity(identity):
                raise SignInError("Mithril could not verify this account. Run mithril login.")
            print("Signed in to Mithril Fund on this Profile.")
            return 0
        require_env_writable(TOKEN_ENV, "set")
        if is_managed():
            raise SignInError("This Profile is managed. Ask its administrator to configure account sign-in.")
        config = require_readable_config_before_write()
        if not args.connectors and not isinstance(config.get("providers", {}), dict):
            raise SignInError("Repair the providers section in config.yaml before signing in.")
        current = config.get("model", {})
        current = current.get("default") if isinstance(current, dict) else current
        if args.connectors and args.model:
            raise SignInError("Use a separate mithril login --model command to change the inference model.")
        model = None if args.connectors else select_model(request("/v1/models"), args.model, current)
        requested = SCOPES | CONNECTOR_SCOPES if args.connectors else SCOPES
        grant = request("/v1/device/code", {"device_name": "Mithril Agent CLI", "scope": " ".join(sorted(requested))})
        url = validate_grant(grant)
        print("Sign in to Mithril Fund and approve this device in your browser.", flush=True)
        print(f"Approval code: {grant['user_code']}\n{url}", flush=True)
        print("Waiting for approval. Press Ctrl+C to cancel.", flush=True)
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except webbrowser.Error:
                pass
        token = poll(grant, required_scopes=requested) if args.connectors else poll(grant)
        identity = request("/v1/me", token=token)
        if not verified_identity(identity, requested):
            raise SignInError("Mithril could not verify this account. No local settings were changed.")
        model = select_model(request("/v1/models", token=token), args.model, current)
        persist(token, model)
        if args.connectors:
            print("Mithril connector access authorized for this Profile. Open Mithril to connect Linear.")
        else:
            suffix = "Continuing to your session." if continue_on_login else "Run mithril to start a new conversation."
            print(f"Signed in to Mithril Fund. Model: {model}\n{suffix}")
        return 0
    except KeyboardInterrupt:
        print("\nSign-in cancelled. Run mithril account status to check this Profile.", file=sys.stderr)
        return 130
    except (SignInError, OSError, ValueError, RuntimeError):
        error = sys.exc_info()[1]
        message = str(error) if isinstance(error, SignInError) else "Could not update this Profile. Check its configuration and permissions."
        print("Mithril Agent: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
