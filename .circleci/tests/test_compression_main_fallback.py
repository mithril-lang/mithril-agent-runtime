"""Compression retries must leave a failed configured auxiliary endpoint."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent import auxiliary_client, context_compressor


@pytest.mark.parametrize("fallback", [False, True])
def test_compression_main_fallback_uses_main_route(monkeypatch, fallback):
    calls = []
    class StopCall(Exception):
        pass
    def capture(**kwargs):
        calls.append(kwargs)
        raise StopCall()
    monkeypatch.setattr(context_compressor, "call_llm", capture)
    monkeypatch.setattr(context_compressor, "aux_interrupt_protection", nullcontext)
    monkeypatch.setattr(auxiliary_client, "_effective_aux_timeout", lambda task, timeout: 900)
    monkeypatch.setattr(auxiliary_client, "_get_auxiliary_task_config", lambda task: {
        "provider": "mithril-inference", "model": "aux-model",
        "base_url": "https://aux.example/v1", "api_key": "aux-key",
    })
    compressor = SimpleNamespace(
        model="main-model", provider="custom", base_url="https://main.example/v1",
        api_key="main-key", api_mode="chat_completions", summary_model="",
        _summary_model_fallen_back=fallback, context_length=262144,
        _record_aux_compression_call=Mock(),
    )
    with pytest.raises(StopCall):
        context_compressor.ContextCompressor._call_summary_llm(compressor, "prompt", 0)
    kwargs = calls[0]
    route = auxiliary_client._resolve_task_provider_model(
        kwargs["task"], kwargs.get("provider"), kwargs.get("model"),
        kwargs.get("base_url"), kwargs.get("api_key"),
    )
    if fallback:
        assert route[:4] == ("custom", "main-model", "https://main.example/v1", "main-key")
        assert kwargs["api_mode"] == "chat_completions"
        assert kwargs["timeout"] == 900
    else:
        assert route[:4] == ("custom", "aux-model", "https://aux.example/v1", "aux-key")
