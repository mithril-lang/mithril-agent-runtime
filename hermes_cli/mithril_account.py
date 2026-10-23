"""Profile-scoped Mithril account token helpers for inference."""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

MITHRIL_API_ORIGIN = "https://api.mithril.fund"
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
