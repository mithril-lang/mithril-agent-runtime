"""Profile-scoped Mithril identity for native connector clients and inference API requests."""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlparse

ORIGIN = "https://api.mithril.fund"
MITHRIL_API_ORIGIN = ORIGIN
MITHRIL_TOKEN_ENV = "MITHRIL_ACCOUNT_TOKEN"


def read_mithril_account_token() -> Optional[str]:
    """Return the active Profile's device-approved token, or None when unavailable.

    Multiplexed requests must have an explicit Profile secret scope. Never fall
    back to an ambient environment value when the scope is missing.
    """
    from agent.secret_scope import (UnscopedSecretError, get_secret,
                                    load_env_file, serves_routed_profile)

    try:
        value = get_secret(MITHRIL_TOKEN_ENV)
    except UnscopedSecretError:
        # An active multiplexer without a bound Profile must fail closed; its process
        # environment may belong to a different Profile.
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    # Login writes this credential to the active Profile's .env. The ordinary
    # single-Profile CLI may read that file, but routed Profiles must use their
    # already-bound secret mapping and never inspect the process home.
    try:
        if serves_routed_profile():
            return None
        from hermes_constants import get_hermes_home
        value = load_env_file(Path(get_hermes_home()) / ".env").get(MITHRIL_TOKEN_ENV)
    except Exception:
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def account_token() -> Optional[str]:
    """Profile-scoped token read used by the native connector clients."""
    return read_mithril_account_token()


def native_connector_url(url) -> bool:
    """True when the URL is Mithril's canonical native connector origin."""
    try:
        parsed = urlsplit(str(url))
        return (parsed.scheme == "https" and parsed.hostname == "api.mithril.fund"
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and (parsed.path in ("", "/") or parsed.path == "/v1/connectors"
                     or parsed.path.startswith(("/v1/connectors/", "/api/v1/connectors/"))))
    except ValueError:
        return False


def mithril_inference_token_for_base_url(base_url: object) -> Optional[str]:
    """Return this Profile's token only for Mithril's canonical HTTPS API origin."""
    try:
        parsed = urlparse(str(base_url or ""))
        if (parsed.scheme.lower() != "https" or parsed.hostname is None
                or parsed.hostname.lower() != "api.mithril.fund"
                or parsed.port not in (None, 443) or parsed.username is not None
                or parsed.password is not None):
            return None
    except ValueError:
        return None
    return read_mithril_account_token()
