"""Mithril chat's character cap is independent of the model's token window."""

from types import SimpleNamespace

from agent.error_classifier import FailoverReason, classify_api_error
from agent.turn_api_error import handle_api_error
from agent.turn_overflow import (
    _Recovery, _chat_input_chars, _recover_input_char_limit, recover_from_overflow,
)
from agent.turn_retry_state import TurnRetryState


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


class _SdkBadRequest(Exception):
    """OpenAI-SDK shape: ``__str__`` is the status line and the sentence is only in ``body``."""

    status_code = 400

    def __init__(self, message: str):
        super().__init__("Error code: 400")
        self.body = {"error": {"code": "input_too_large", "message": message}}


def test_body_only_character_cap_reaches_compression_through_the_caller(monkeypatch):
    sentence = "chat input is 524889 characters; this model accepts at most 524288"
    api_error = _SdkBadRequest(sentence)
    assert "chat input is" not in str(api_error).lower()
    assert classify_api_error(api_error, provider="custom").reason == FailoverReason.context_overflow

    statuses = []
    messages = [{"role": "user", "content": "x" * 1000}]
    agent = SimpleNamespace(
        model="xiaomi/mimo-v2.6-pro", provider="custom", base_url="https://chat.example",
        api_mode="chat_completions", api_key=None, log_prefix="", thinking_callback=None,
        session_id="", platform="cli", tools=None, compression_enabled=True,
        _image_rejecting_models=set(), _unicode_sanitization_passes=0,
        _interrupt_requested=False, context_compressor=SimpleNamespace(context_length=1048576),
        _buffer_diagnostic_status=statuses.append,
        _buffer_vprint=lambda *_a, **_k: None,
        _vprint=lambda *_a, **_k: None,
        _touch_activity=lambda *_a, **_k: None,
        _invoke_api_request_error_hook=lambda *_a, **_k: None,
        _extract_api_error_context=lambda _error: {},
        _summarize_api_error=lambda error: str(error)[:200],
        _client_log_context=lambda: "",
        _is_openrouter_url=lambda: False,
        _recover_with_credential_pool=lambda **_k: (False, False),
    )

    def compress(self, _tokens, *, fail_on_timeout=False):
        assert fail_on_timeout
        self.messages = [{"role": "user", "content": "x" * 399}]
        return None

    monkeypatch.setattr(_Recovery, "request_tokens", lambda self: 273400)
    monkeypatch.setattr(_Recovery, "compress", compress)
    retry = TurnRetryState()
    verdict = handle_api_error(
        agent, api_error=api_error, _retry=retry, thinking_spinner=None, messages=messages,
        api_messages=list(messages), api_kwargs={}, system_message="", active_system_prompt="",
        conversation_history=[], approx_tokens=273400, retry_count=0, max_retries=3,
        compression_attempts=0, max_compression_attempts=3, api_call_count=1,
        api_request_id="req", api_start_time=0, effective_task_id=None, turn_id="turn",
    )
    assert verdict.action == "break"
    assert retry.restart_with_compressed_messages
    assert verdict.compression_attempts == 1
    assert _chat_input_chars(verdict.messages) == 399
    assert any("601" in status for status in statuses)


def test_character_progress_uses_assembled_wire_text_not_persisted_content(monkeypatch):
    """The route joined the system prompt plus ``api_content``, not persisted ``content``.

    Prompt 500→100 and sidecar 1000→399 is a 1,001-unit wire drop. Persisted ``hi``
    growing to 399 is not progress, and ignoring the prompt leaves a 601-unit drop.
    """
    statuses = []
    prompt = "s" * 500
    messages = [{"role": "user", "content": "hi", "api_content": "x" * 1000}]
    api_messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "x" * 1000},
    ]
    assert _chat_input_chars(messages) == 1000  # sidecar, not the 2-character transcript
    assert _chat_input_chars(api_messages) == 1500
    agent = SimpleNamespace(
        model="xiaomi/mimo-v2.6-pro", log_prefix="", tools=None,
        _buffer_diagnostic_status=statuses.append,
    )

    def compress(self, _tokens, *, fail_on_timeout=False):
        assert fail_on_timeout
        self.active_system_prompt = "s" * 100
        self.messages = [{"role": "user", "content": "x" * 399}]
        return None

    monkeypatch.setattr(_Recovery, "request_tokens", lambda self: 273400)
    monkeypatch.setattr(_Recovery, "compress", compress)
    retry = TurnRetryState()
    verdict = recover_from_overflow(
        agent, Exception("unused"), SimpleNamespace(reason=FailoverReason.context_overflow), retry,
        status_code=400,
        error_msg="chat input is 1500 characters; this model accepts at most 524288",
        wrapped_output_cap_budget=None, messages=messages, api_messages=api_messages,
        system_message=prompt, active_system_prompt=prompt, conversation_history=[],
        approx_tokens=273400, compression_attempts=0, max_compression_attempts=3,
        api_call_count=1, effective_task_id=None,
    )
    assert verdict.action == "break"
    assert retry.restart_with_compressed_messages
    assert any("1,001" in status for status in statuses)
    assert not any("601" in status for status in statuses)
