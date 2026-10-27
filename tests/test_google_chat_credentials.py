"""Keyless Google Chat credentials stay bound to the selected profile file."""

import json

import google.auth

from plugins.platforms.google_chat.adapter import _load_sa_credentials_from


def test_impersonated_adc_file_is_profile_bound(tmp_path, monkeypatch):
    def no_global_adc(*_args, **_kwargs):
        raise AssertionError("explicit Google Chat credentials must not read global ADC")

    monkeypatch.setattr(google.auth, "default", no_global_adc)

    files = []
    for name in ("alpha", "beta"):
        target = f"{name}@example-project.iam.gserviceaccount.com"
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({
            "type": "impersonated_service_account",
            "service_account_impersonation_url": (
                "https://iamcredentials.googleapis.com/v1/projects/-/"
                f"serviceAccounts/{target}:generateAccessToken"
            ),
            "source_credentials": {
                "type": "authorized_user",
                "client_id": "test.apps.googleusercontent.com",
                "client_secret": "test",
                "refresh_token": "test",
            },
        }), encoding="utf-8")
        files.append(path)

    assert [
        _load_sa_credentials_from(str(path)).service_account_email
        for path in (files[0], files[1], files[0])
    ] == [
        "alpha@example-project.iam.gserviceaccount.com",
        "beta@example-project.iam.gserviceaccount.com",
        "alpha@example-project.iam.gserviceaccount.com",
    ]
