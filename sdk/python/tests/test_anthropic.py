import anthropic
import pytest

from agenttrace import SpanKind, SpanStatus, instrument_anthropic, span

MESSAGE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-5-5",
    "content": [{"type": "text", "text": "Hello"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 12, "output_tokens": 3},
}


def client(url: str) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key="test", base_url=url, max_retries=0)


def test_messages_create_becomes_an_llm_span(collected, http_server):
    instrument_anthropic()
    instrument_anthropic()  # idempotent: one span per call, not two
    url = http_server(lambda p, h, b: (200, MESSAGE))

    with span("agent", "agent"):
        client(url).messages.create(
            model="claude-opus-5-5",
            max_tokens=100,
            system="be brief",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "lookup", "input_schema": {"type": "object"}}],
        )

    llm = collected.by_name("anthropic.messages.create")
    assert llm.kind == SpanKind.LLM
    assert llm.parent_span_id == collected.by_name("agent").span_id
    assert llm.model == "claude-opus-5-5"
    assert (llm.input_tokens, llm.output_tokens) == (12, 3)
    assert llm.input == {"system": "be brief", "messages": [{"role": "user", "content": "hi"}]}
    assert llm.output == [{"type": "text", "text": "Hello"}]
    assert llm.attributes["max_tokens"] == "100"
    assert llm.attributes["llm.tools"] == "lookup"
    assert llm.attributes["llm.stop_reason"] == "end_turn"


def test_the_serving_model_is_recorded_not_the_requested_one(collected, http_server):
    instrument_anthropic()
    url = http_server(lambda p, h, b: (200, {**MESSAGE, "model": "claude-opus-4-8"}))
    client(url).beta.messages.create(
        model="claude-opus-5-5", max_tokens=10, messages=[{"role": "user", "content": "hi"}]
    )
    assert collected.spans[0].model == "claude-opus-4-8"


def test_api_errors_mark_the_span_error(collected, http_server):
    instrument_anthropic()
    error = {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
    url = http_server(lambda p, h, b: (400, error))
    with pytest.raises(anthropic.BadRequestError):
        client(url).messages.create(
            model="claude-opus-5-5", max_tokens=10, messages=[{"role": "user", "content": "hi"}]
        )
    s = collected.spans[0]
    assert s.status == SpanStatus.ERROR and s.error_type == "BadRequestError"
    assert s.model == "claude-opus-5-5"
