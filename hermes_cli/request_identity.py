"""Product identity helpers for outbound Agent requests.

The active distribution owns the client identity. Protocol-specific headers remain
in their provider adapters; these helpers only select the product token and version.
"""

from __future__ import annotations

import os


def _mithril_mode() -> bool:
    return os.environ.get("MITHRIL_AGENT") == "1"


def _base_version() -> str | None:
    try:
        from hermes_cli.version_info import get_version_info

        return get_version_info().base_version
    except Exception:
        return None


def agent_user_agent() -> str:
    """Return the versioned User-Agent for the active Agent distribution."""
    product = "MithrilAgent" if _mithril_mode() else "HermesAgent"
    return f"{product}/{_base_version() or 'unknown'}"


def router_user_agent() -> str:
    """Return Router's coding-agent UA, preserving Hermes' legacy spelling."""
    product = "MithrilAgent" if _mithril_mode() else "Hermes-Agent"
    return f"{product}/{_base_version() or 'unknown'}"


def copilot_user_agent() -> str:
    """Return Copilot's client UA without changing the legacy Hermes version token."""
    if not _mithril_mode():
        return "HermesAgent/1.0"
    return agent_user_agent()


def cli_user_agent() -> str:
    """Return the CLI-style User-Agent used for provider catalog requests."""
    product = "mithril-agent" if _mithril_mode() else "hermes-cli"
    version = _base_version()
    return f"{product}/{version}" if version else product


def agent_client_identifier() -> str:
    """Return the lower-case product identifier used by provider client metadata."""
    product = "mithril-agent" if _mithril_mode() else "hermes-agent"
    return f"{product}/{_base_version() or 'unknown'}"
