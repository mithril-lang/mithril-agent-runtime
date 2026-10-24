"""Mithril chat's character cap is independent of the model's token window."""

from types import SimpleNamespace

from agent.error_classifier import FailoverReason, classify_api_error
from agent.turn_overflow import _Recovery, _chat_input_chars, _recover_input_char_limit


class _BadRequest(Exception):
    status_code = 400

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.body = {"error": {"code": code, "message": message}}


def test_measured_chat_character_cap_routes_to_compression():
    message = "chat input is 524889 characters; this model accepts at most 524288. Start a new conversation or compress it"
    classified = classify_api_error(
        _BadRequest("input_too_large", message), provider="custom",
        model="xiaomi/mimo-v2.6-pro", approx_tokens=273400, context_length=1048576,
    )
    assert classified.reason == FailoverReason.context_overflow
    assert classified.should_compress

    flattened = _BadRequest("input_too_large", message)
    flattened.body = None
    assert classify_api_error(flattened, provider="custom").reason == FailoverReason.context_overflow

    unrelated = classify_api_error(
        _BadRequest("input_too_large", "chat request body exceeds 1048576 bytes"),
        provider="custom", model="xiaomi/mimo-v2.6-pro",
    )
    assert unrelated.reason != FailoverReason.context_overflow


def test_character_recovery_retries_a_small_but_sufficient_reduction(monkeypatch):
    statuses = []
    agent = SimpleNamespace(
        model="xiaomi/mimo-v2.6-pro", log_prefix="",
        _buffer_diagnostic_status=statuses.append,
    )
    messages = [{"role": "system", "content": "😀"}, {"role": "user", "content": "x" * 1000}]
    original_chars = _chat_input_chars(messages)
    assert original_chars == 1002  # API uses JavaScript UTF-16 character counts.
    state = _Recovery(
        messages=messages, active_system_prompt="", conversation_history=[],
        approx_tokens=273400, compression_attempts=0, provider_overflow_recovery_pending=False,
        is_context_length_error=True, agent=agent, api_messages=messages, system_message="",
        effective_task_id=None, api_call_count=1, max_compression_attempts=3,
    )

    def compress(self, _tokens, *, fail_on_timeout=False):
        assert fail_on_timeout
        self.messages = [{"role": "system", "content": "😀"}, {"role": "user", "content": "x" * 399}]
        return None

    monkeypatch.setattr(_Recovery, "request_tokens", lambda self: 273400)
    monkeypatch.setattr(_Recovery, "compress", compress)
    retry = SimpleNamespace(restart_with_compressed_messages=False)
    verdict = _recover_input_char_limit(
        state, retry, "chat input is 524889 characters; this model accepts at most 524288"
    )
    assert verdict.action == "break"
    assert retry.restart_with_compressed_messages
    assert verdict.compression_attempts == 1
    assert _chat_input_chars(verdict.messages) == original_chars - 601
    assert any("601" in status for status in statuses)
