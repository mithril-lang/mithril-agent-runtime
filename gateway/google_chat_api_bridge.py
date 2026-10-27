"""Route one operator's Google Chat DM into the already-running local gateway.

This optional sidecar owns only the Google Chat Pub/Sub subscription. It leaves
the multiplex gateway and its other messaging adapters running unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
import signal
import stat

from dotenv import dotenv_values
import httpx

from gateway.config import PlatformConfig
from plugins.platforms.google_chat.adapter import GoogleChatAdapter

_API_URL = "http://127.0.0.1:8642/v1/chat/completions"
_LOG = logging.getLogger(__name__)


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _api_key_from_file(path: Path) -> str:
    metadata = path.stat()
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("API credential file must be owned by this user and mode 0600")
    key = str(dotenv_values(path).get("API_SERVER_KEY") or "").strip()
    if len(key) < 16 or key.startswith("op://"):
        raise ValueError("API_SERVER_KEY is missing or unresolved in the credential file")
    return key


def _session_id(chat_id: str, sender_email: str) -> str:
    digest = hashlib.sha256(f"{chat_id}\0{sender_email}".encode()).hexdigest()[:32]
    return f"gchat_dm_{digest}"


def _request_headers(api_key: str, session_id: str, message_id: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {api_key}", "X-Hermes-Session-Id": session_id}
    if message_id:
        headers["Idempotency-Key"] = hashlib.sha256(message_id.encode()).hexdigest()
    return headers


def _response_text(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise ValueError("gateway response has no choice")
    content = (choices[0].get("message") or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("gateway response is empty")
    return content.strip()


def _approved_inbound(msg: dict, envelope: dict, allowed_email: str) -> bool:
    """Reject other spaces before the adapter downloads any attachment."""
    space = envelope.get("space") or msg.get("space") or {}
    space_type = str(space.get("type") or space.get("spaceType") or "").upper()
    sender = msg.get("sender") or {}
    return (space_type in {"DIRECT_MESSAGE", "DM"}
            and str(sender.get("email") or "").strip().lower() == allowed_email)


class PersonalGoogleChatAdapter(GoogleChatAdapter):
    def __init__(self, config: PlatformConfig, allowed_email: str):
        super().__init__(config)
        self._bridge_allowed_email = allowed_email

    async def _dispatch_message(self, msg: dict, envelope: dict) -> None:
        if not _approved_inbound(msg, envelope, self._bridge_allowed_email):
            return
        await super()._dispatch_message(msg, envelope)


async def _answer(event, *, allowed_email: str, api_key: str, client: httpx.AsyncClient) -> str | None:
    source = event.source
    if source is None or source.chat_type != "dm":
        return None
    email = (source.user_id or "").strip().lower()
    if email != allowed_email or not source.chat_id.startswith("spaces/"):
        return None
    if event.media_urls:
        return "この接続ではテキストメッセージを送ってください。"
    prompt = (event.text or "").strip()
    if not prompt:
        return None
    try:
        response = await client.post(
            _API_URL,
            headers=_request_headers(api_key, _session_id(source.chat_id, email), event.message_id or ""),
            json={"messages": [{"role": "user", "content": prompt}], "stream": False},
        )
        response.raise_for_status()
        return _response_text(response.json())
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        _LOG.exception("Local Mithril gateway request failed for Google Chat DM")
        return "Mithril Agent に一時的に接続できません。少し待ってから再送してください。"


async def run() -> None:
    allowed_email = _required_env("GOOGLE_CHAT_BRIDGE_ALLOWED_EMAIL").lower()
    if "," in allowed_email or "@" not in allowed_email:
        raise ValueError("GOOGLE_CHAT_BRIDGE_ALLOWED_EMAIL must name one email address")
    project_id = _required_env("GOOGLE_CHAT_PROJECT_ID")
    subscription = _required_env("GOOGLE_CHAT_SUBSCRIPTION_NAME")
    credential_path = _required_env("GOOGLE_CHAT_SERVICE_ACCOUNT_JSON")
    api_key = _api_key_from_file(Path(_required_env("GOOGLE_CHAT_BRIDGE_API_ENV_FILE")))
    adapter = PersonalGoogleChatAdapter(PlatformConfig(enabled=True, typing_indicator=False, extra={
        "project_id": project_id,
        "subscription_name": subscription,
        "service_account_json": credential_path,
    }), allowed_email)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0), trust_env=False) as client:
        async def handle(event):
            return await _answer(event, allowed_email=allowed_email, api_key=api_key, client=client)

        adapter.set_message_handler(handle)
        if not await adapter.connect():
            raise RuntimeError("Google Chat adapter connection failed")
        try:
            stop_task = asyncio.create_task(stop.wait())
            try:
                await asyncio.wait(
                    {stop_task, adapter._supervisor_task}, return_when=asyncio.FIRST_COMPLETED,
                )
                if not stop.is_set():
                    raise RuntimeError("Google Chat Pub/Sub listener stopped")
            finally:
                stop_task.cancel()
        finally:
            await adapter.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())
