"""Regression tests for routing Mithril device-login tokens to inference calls."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.auxiliary_client import _named_custom_api_key
from agent.secret_scope import reset_secret_scope, set_multiplex_active, set_secret_scope
from hermes_cli.mithril_account import mithril_inference_token_for_base_url
import hermes_cli.runtime_provider_custom as custom_runtime


TOKEN = "mf_test_token_1234567890"


class MithrilAccountTokenTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.env_patch = patch.dict(os.environ, {"HERMES_HOME": self.home.name}, clear=False)
        self.env_patch.start()
        os.environ.pop("MITHRIL_ACCOUNT_TOKEN", None)
        self.scope_token = set_secret_scope(None)
        set_multiplex_active(False)

    def tearDown(self):
        set_multiplex_active(False)
        reset_secret_scope(self.scope_token)
        self.env_patch.stop()
        self.home.cleanup()

    def write_profile_token(self, token=TOKEN):
        Path(self.home.name, ".env").write_text(
            f"MITHRIL_ACCOUNT_TOKEN={token}\n", encoding="utf-8"
        )

    def test_account_token_is_available_from_the_profile_env(self):
        self.write_profile_token()
        self.assertEqual(
            mithril_inference_token_for_base_url("https://api.mithril.fund/v1"), TOKEN
        )

    def test_token_is_restricted_to_the_canonical_https_origin(self):
        self.write_profile_token()
        for base_url in (
            "http://api.mithril.fund/v1",
            "https://api.mithril.fund.evil.example/v1",
            "https://api.mithril.fund:444/v1",
            "https://other.example/v1",
        ):
            with self.subTest(base_url=base_url):
                self.assertIsNone(mithril_inference_token_for_base_url(base_url))

    def test_auxiliary_custom_provider_prefers_account_token_over_legacy_key(self):
        self.write_profile_token()
        api_key = _named_custom_api_key(
            {"api_key": "legacy-rejected-key"},
            "Mithril",
            "https://api.mithril.fund/v1",
        )
        self.assertEqual(api_key, TOKEN)

    def test_auxiliary_custom_provider_keeps_legacy_key_for_other_hosts(self):
        self.write_profile_token()
        api_key = _named_custom_api_key(
            {"api_key": "legacy-provider-key"},
            "Custom",
            "https://provider.example/v1",
        )
        self.assertEqual(api_key, "legacy-provider-key")

    def test_unscoped_multiplexing_does_not_read_process_token(self):
        os.environ["MITHRIL_ACCOUNT_TOKEN"] = TOKEN
        set_multiplex_active(True)
        self.assertIsNone(
            mithril_inference_token_for_base_url("https://api.mithril.fund/v1")
        )

    def test_named_runtime_custom_provider_uses_account_token(self):
        self.write_profile_token()
        runtime = SimpleNamespace(
            _get_named_custom_provider=lambda _name: {
                "name": "Mithril",
                "base_url": "https://api.mithril.fund/v1",
                "api_key": "legacy-rejected-key",
            },
            _try_resolve_from_custom_pool=lambda *args, **kwargs: None,
            _resolves_to_custom=lambda _name: False,
            _host_gated_env_key_candidates=lambda *args, **kwargs: [],
            has_usable_secret=lambda value: bool(value),
            _get_model_config=lambda: {},
        )
        with (
            patch.object(custom_runtime, "_rp", return_value=runtime),
            patch.object(custom_runtime, "_custom_runtime",
                         side_effect=lambda _rp, url, key, *_args, **_kwargs: {
                             "base_url": url, "api_key": key,
                         }),
            patch.object(custom_runtime, "_apply_custom_provider_extras"),
            patch.object(custom_runtime, "_opencode_family_for_custom", return_value=None),
        ):
            result = custom_runtime._resolve_named_custom_runtime(
                requested_provider="Mithril"
            )
        self.assertEqual(result["api_key"], TOKEN)


if __name__ == "__main__":
    unittest.main()
