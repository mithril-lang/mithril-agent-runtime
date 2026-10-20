"""Profile-scoped Mithril account token helpers for inference API requests."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

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
