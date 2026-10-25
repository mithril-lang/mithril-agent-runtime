"""Mithril account tokens remain Profile-scoped and only reach Mithril's API."""

from agent import secret_scope
from hermes_cli import mithril_account


def test_single_profile_reads_saved_token_from_its_private_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("MITHRIL_ACCOUNT_TOKEN=mf_profile-token-1234567890\n")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("MITHRIL_ACCOUNT_TOKEN", raising=False)
    monkeypatch.setattr(secret_scope, "serves_routed_profile", lambda: False)

    assert mithril_account.read_mithril_account_token() == "mf_profile-token-1234567890"


def test_routed_profile_never_falls_back_to_process_home_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("MITHRIL_ACCOUNT_TOKEN=mf_launch-profile-token\n")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("MITHRIL_ACCOUNT_TOKEN", raising=False)
    monkeypatch.setattr(secret_scope, "serves_routed_profile", lambda: True)
    monkeypatch.setattr(secret_scope, "get_secret", lambda *args, **kwargs: None)

    assert mithril_account.read_mithril_account_token() is None


def test_account_token_is_returned_only_for_canonical_https_origin(monkeypatch):
    monkeypatch.setattr(mithril_account, "read_mithril_account_token", lambda: "mf_profile-token")

    assert mithril_account.mithril_inference_token_for_base_url("https://api.mithril.fund/v1") == "mf_profile-token"
    for url in (
        "http://api.mithril.fund/v1",
        "https://api.mithril.fund.attacker.test/v1",
        "https://api.mithril.fund:444/v1",
        "https://user@api.mithril.fund/v1",
    ):
        assert mithril_account.mithril_inference_token_for_base_url(url) is None


def test_named_mithril_route_prefers_device_token_to_legacy_custom_key(tmp_path, monkeypatch):
    from hermes_cli import runtime_provider as rp

    (tmp_path / ".env").write_text("MITHRIL_ACCOUNT_TOKEN=mf_profile-token-1234567890\n")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("MITHRIL_API_KEY", "legacy-rejected-key")
    monkeypatch.setattr(rp, "_get_named_custom_provider", lambda _: {
        "name": "Mithril Account",
        "base_url": "https://api.mithril.fund/v1",
        "key_env": "MITHRIL_API_KEY",
    })
    monkeypatch.setattr(rp, "_get_model_config", lambda: {})
    monkeypatch.setattr(rp, "_try_resolve_from_custom_pool", lambda *args, **kwargs: None)

    result = rp._resolve_named_custom_runtime(requested_provider="Mithril Account")

    assert result["api_key"] == "mf_profile-token-1234567890"


def test_named_non_mithril_route_never_gets_device_token(tmp_path, monkeypatch):
    from hermes_cli import runtime_provider as rp

    (tmp_path / ".env").write_text("MITHRIL_ACCOUNT_TOKEN=mf_profile-token-1234567890\n")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("OTHER_API_KEY", "other-provider-key")
    monkeypatch.setattr(rp, "_get_named_custom_provider", lambda _: {
        "name": "Other API",
        "base_url": "https://api.example.test/v1",
        "key_env": "OTHER_API_KEY",
    })
    monkeypatch.setattr(rp, "_get_model_config", lambda: {})
    monkeypatch.setattr(rp, "_try_resolve_from_custom_pool", lambda *args, **kwargs: None)

    result = rp._resolve_named_custom_runtime(requested_provider="Other API")

    assert result["api_key"] == "other-provider-key"
