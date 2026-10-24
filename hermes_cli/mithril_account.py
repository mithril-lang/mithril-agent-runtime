"""Profile-scoped Mithril identity for native connector clients and inference API requests."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit, urlparse

ORIGIN = "https://api.mithril.fund"
MITHRIL_TOKEN_ENV = "MITHRIL_ACCOUNT_TOKEN"


def read_mithril_account_token() -> Optional[str]:
    """Return this Profile's device-approved token, or None when unavailable."""
    from agent.secret_scope import UnscopedSecretError, get_secret

    try:
        value = get_secret(MITHRIL_TOKEN_ENV)
    except UnscopedSecretError:
        # An active multiplexer without a bound Profile must fail closed; its process
        # environment may belong to a different Profile.
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
    """Use the account token only for Mithril's canonical HTTPS inference API."""
    try:
        parsed = urlparse(str(base_url or ""))
        if (parsed.scheme != "https" or parsed.hostname != "api.mithril.fund"
                or parsed.port not in (None, 443)):
            return None
    except ValueError:
        return None
    return read_mithril_account_token()
