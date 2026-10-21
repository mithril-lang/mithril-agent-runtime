"""Profile-scoped Mithril identity for native connector clients."""
from urllib.parse import urlsplit

ORIGIN = "https://api.mithril.fund"


def account_token():
    from agent.secret_scope import UnscopedSecretError, get_secret
    try:
        value = get_secret("MITHRIL_ACCOUNT_TOKEN")
    except UnscopedSecretError:
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def native_connector_url(url):
    try:
        parsed = urlsplit(str(url))
        return (parsed.scheme == "https" and parsed.hostname == "api.mithril.fund"
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and (parsed.path in ("", "/") or parsed.path == "/v1/connectors"
                     or parsed.path.startswith(("/v1/connectors/", "/api/v1/connectors/"))))
    except ValueError:
        return False
