"""Personal DM bridge cannot send other Chat conversations to the local agent."""

import json
from types import SimpleNamespace

import httpx
import pytest

from gateway.google_chat_api_bridge import _answer, _api_key_from_file, _approved_inbound, _session_id


def _event(email="j@kotobalabs.com", chat_type="dm", text="ping"):
    return SimpleNamespace(
        source=SimpleNamespace(user_id=email, chat_type=chat_type, chat_id="spaces/operator-dm"),
        text=text, media_urls=[], message_id="spaces/operator-dm/messages/first",
    )


@pytest.mark.asyncio
async def test_only_approved_dm_reaches_gateway():
    requests = []

    def gateway(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(gateway)) as client:
        assert await _answer(_event(email="other@example.com"), allowed_email="j@kotobalabs.com",
                             api_key="local-test-key", client=client) is None
        assert await _answer(_event(chat_type="group"), allowed_email="j@kotobalabs.com",
                             api_key="local-test-key", client=client) is None
        assert requests == []
        assert await _answer(_event(), allowed_email="j@kotobalabs.com",
                             api_key="local-test-key", client=client) == "pong"

    assert len(requests) == 1
    assert requests[0].headers["X-Hermes-Session-Id"] == _session_id("spaces/operator-dm", "j@kotobalabs.com")
    assert requests[0].headers["Idempotency-Key"]
    assert json.loads(requests[0].content)["messages"] == [{"role": "user", "content": "ping"}]


def test_bridge_requires_private_local_gateway_credential_file(tmp_path):
    path = tmp_path / "gateway.env"
    path.write_text("API_SERVER_KEY=example-test-secret-123\n", encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="mode 0600"):
        _api_key_from_file(path)
    path.chmod(0o600)
    assert _api_key_from_file(path) == "example-test-secret-123"


def test_unapproved_space_or_sender_is_rejected_before_attachment_fetch():
    msg = {"sender": {"email": "j@kotobalabs.com"}, "attachment": [{"name": "private"}]}
    assert _approved_inbound(msg, {"space": {"spaceType": "DIRECT_MESSAGE"}}, "j@kotobalabs.com")
    assert not _approved_inbound(msg, {"space": {"spaceType": "SPACE"}}, "j@kotobalabs.com")
    assert not _approved_inbound(msg, {"space": {"spaceType": "DIRECT_MESSAGE"}}, "other@example.com")
