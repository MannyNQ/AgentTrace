import anthropic
import pytest

from trayce import SpanKind, SpanStatus, instrument_anthropic, span

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
    assert llm.input == {
        "model": "claude-opus-5-5",
        "max_tokens": 100,
        "system": "be brief",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "lookup", "input_schema": {"type": "object"}}],
    }
    assert list(llm.input)[:2] == ["system", "messages"]  # the preview shows the conversation
    assert llm.output == {k: v for k, v in MESSAGE.items() if v is not None}
    assert llm.attributes["llm.tools"] == "lookup"
    assert llm.attributes["llm.stop_reason"] == "end_turn"
    assert llm.attributes["trayce.interceptor"] == "anthropic"


def test_transport_options_and_unset_sentinels_stay_out_of_the_input(collected, http_server):
    instrument_anthropic()
    url = http_server(lambda p, h, b: (200, MESSAGE))
    client(url).messages.create(
        model="claude-opus-5-5",
        max_tokens=10,
        messages=[{"role": "user", "content": "hi"}],
        stop_sequences=anthropic.NOT_GIVEN,
        extra_headers={"x-secret": "s"},
        timeout=5,
    )
    assert set(collected.spans[0].input) == {"model", "max_tokens", "messages"}


def test_the_recorded_response_rebuilds_the_message(collected, http_server):
    instrument_anthropic()
    tool_use = {"type": "tool_use", "id": "t1", "name": "lookup", "input": {"id": "7"}}
    reply = {**MESSAGE, "content": [tool_use], "stop_reason": "tool_use"}
    url = http_server(lambda p, h, b: (200, reply))
    response = client(url).messages.create(
        model="claude-opus-5-5", max_tokens=10, messages=[{"role": "user", "content": "hi"}]
    )
    assert anthropic.types.Message.model_validate(collected.spans[0].output) == response


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


def test_cache_tokens_are_recorded_by_ttl(collected, http_server):
    instrument_anthropic()
    usage = {
        "input_tokens": 5,
        "output_tokens": 3,
        "cache_read_input_tokens": 1000,
        "cache_creation_input_tokens": 300,
        "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 200},
    }
    url = http_server(lambda p, h, b: (200, {**MESSAGE, "usage": usage}))
    client(url).messages.create(
        model="claude-opus-5-5", max_tokens=10, messages=[{"role": "user", "content": "hi"}]
    )
    s = collected.spans[0]
    assert (s.cache_read_tokens, s.cache_write_5m_tokens, s.cache_write_1h_tokens) == (
        1000,
        100,
        200,
    )


def test_cache_writes_without_a_ttl_breakdown_count_as_5m(collected, http_server):
    instrument_anthropic()
    usage = {"input_tokens": 5, "output_tokens": 3, "cache_creation_input_tokens": 300}
    url = http_server(lambda p, h, b: (200, {**MESSAGE, "usage": usage}))
    client(url).messages.create(
        model="claude-opus-5-5", max_tokens=10, messages=[{"role": "user", "content": "hi"}]
    )
    s = collected.spans[0]
    assert (s.cache_write_5m_tokens, s.cache_write_1h_tokens) == (300, None)
