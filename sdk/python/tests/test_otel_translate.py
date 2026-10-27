import json
from datetime import UTC, datetime, timedelta

import pytest

from agenttrace import SpanKind, SpanStatus
from agenttrace.otel import OtelEvent, OtelSpan, Unstorable, translate

TRACE = "0af7651916cd43dd8448eb211c80319c"
ROOT = "b7ad6b7169203331"
CHILD = "00f067aa0ba902b7"
T0_NS = 1_767_225_600_000_000_000  # 2026-01-01T00:00:00Z
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def otel(**overrides) -> OtelSpan:
    fields = {
        "trace_id": TRACE,
        "span_id": CHILD,
        "parent_span_id": ROOT,
        "name": "work",
        "start_time_ns": T0_NS,
        "end_time_ns": T0_NS + 1_500_000_000,
    }
    return OtelSpan(**(fields | overrides))


# Attributes as Pydantic AI records them (GenAI conventions, content capture on).
GEN_AI_CHAT = {
    "gen_ai.operation.name": "chat",
    "gen_ai.provider.name": "anthropic",
    "gen_ai.request.model": "claude-sonnet-5-5",
    "gen_ai.response.model": "claude-sonnet-5-5-20260901",
    "gen_ai.usage.input_tokens": 1500,
    "gen_ai.usage.output_tokens": 40,
    "gen_ai.usage.cache_read.input_tokens": 1000,
    "gen_ai.usage.cache_creation.input_tokens": 300,
    "gen_ai.system_instructions": json.dumps([{"type": "text", "content": "be brief"}]),
    "gen_ai.input.messages": json.dumps(
        [{"role": "user", "parts": [{"type": "text", "content": "where is order 42?"}]}]
    ),
    "gen_ai.output.messages": json.dumps(
        [{"role": "assistant", "parts": [{"type": "tool_call", "name": "lookup_order"}]}]
    ),
    "gen_ai.request.temperature": 0.2,
}

GEN_AI_TOOL = {
    "gen_ai.operation.name": "execute_tool",
    "gen_ai.tool.name": "lookup_order",
    "gen_ai.tool.call.id": "call_1",
    "gen_ai.tool.call.arguments": json.dumps({"order_id": "42"}),
    "gen_ai.tool.call.result": json.dumps({"status": "shipped"}),
}

# Attributes as OpenInference's LangChain instrumentor records them.
OPENINFERENCE_LLM = {
    "openinference.span.kind": "LLM",
    "llm.model_name": "gpt-5.1",
    "llm.token_count.prompt": 900,
    "llm.token_count.completion": 20,
    "llm.token_count.total": 920,
    "llm.token_count.prompt_details.cache_read": 800,
    "input.value": json.dumps({"messages": [{"role": "user", "content": "hi"}]}),
    "input.mime_type": "application/json",
    "output.value": "hello",
    "output.mime_type": "text/plain",
}

OPENINFERENCE_TOOL = {
    "openinference.span.kind": "TOOL",
    "tool.name": "lookup_order",
    "input.value": json.dumps({"order_id": "42"}),
    "input.mime_type": "application/json",
    "output.value": "shipped",
}


def test_a_gen_ai_chat_span_becomes_an_llm_span():
    s = translate(otel(name="chat claude-sonnet-5-5", attributes=GEN_AI_CHAT))
    assert s.kind == SpanKind.LLM
    assert s.name == "chat claude-sonnet-5-5"
    assert (s.trace_id, s.span_id, s.parent_span_id) == (TRACE, CHILD, ROOT)
    assert s.start_time == T0 and s.end_time == T0 + timedelta(seconds=1.5)
    # The model that answered, not the one requested, since that's what was billed.
    assert s.model == "claude-sonnet-5-5-20260901"
    assert s.input == {
        "system": json.loads(GEN_AI_CHAT["gen_ai.system_instructions"]),
        "messages": json.loads(GEN_AI_CHAT["gen_ai.input.messages"]),
    }
    assert s.output == json.loads(GEN_AI_CHAT["gen_ai.output.messages"])
    assert s.attributes["agenttrace.convention"] == "gen_ai"
    assert s.attributes["gen_ai.request.temperature"] == "0.2"


def test_cached_tokens_are_taken_out_of_input_tokens():
    s = translate(otel(attributes=GEN_AI_CHAT))
    assert s.input_tokens == 200  # 1500 reported, minus 1000 read and 300 written
    assert s.output_tokens == 40
    assert s.cache_read_tokens == 1000
    # No convention says which TTL a write used; 5 minutes is the default.
    assert (s.cache_write_5m_tokens, s.cache_write_1h_tokens) == (300, None)


@pytest.mark.parametrize(
    "write_key",
    [
        "gen_ai.usage.cache_write.input_tokens",
        "gen_ai.usage.cache_creation.input_tokens",
        "gen_ai.usage.cache_creation_input_tokens",
    ],
)
def test_old_and_new_cache_write_names_are_read(write_key):
    attrs = {
        "gen_ai.operation.name": "chat",
        "gen_ai.usage.input_tokens": 50,
        "gen_ai.usage.output_tokens": 1,
        write_key: 30,
    }
    s = translate(otel(attributes=attrs))
    assert (s.input_tokens, s.cache_write_5m_tokens) == (20, 30)


def test_input_tokens_never_go_below_zero():
    attrs = {
        "gen_ai.operation.name": "chat",
        "gen_ai.usage.input_tokens": 10,
        "gen_ai.usage.output_tokens": 1,
        "gen_ai.usage.cache_read.input_tokens": 50,
    }
    assert translate(otel(attributes=attrs)).input_tokens == 0


def test_the_request_model_is_used_without_a_response_model():
    attrs = {"gen_ai.operation.name": "chat", "gen_ai.request.model": "claude-haiku-5-5"}
    assert translate(otel(attributes=attrs)).model == "claude-haiku-5-5"


def test_a_gen_ai_tool_span_is_named_for_its_tool_with_parsed_arguments():
    s = translate(otel(name="execute_tool lookup_order", attributes=GEN_AI_TOOL))
    assert s.kind == SpanKind.TOOL
    # Trajectories read the tool from the span name (ADR-0022).
    assert s.name == "lookup_order"
    assert s.input == {"order_id": "42"}
    assert s.output == {"status": "shipped"}
    assert s.model is None and s.input_tokens is None


def test_content_moves_out_of_attributes():
    chat = translate(otel(attributes=GEN_AI_CHAT)).attributes
    tool = translate(otel(attributes=GEN_AI_TOOL)).attributes
    oi = translate(otel(attributes=OPENINFERENCE_LLM)).attributes
    for moved in ("gen_ai.input.messages", "gen_ai.output.messages", "gen_ai.system_instructions"):
        assert moved not in chat
    assert "gen_ai.tool.call.arguments" not in tool and "gen_ai.tool.call.result" not in tool
    assert "input.value" not in oi and "output.value" not in oi
    assert tool["gen_ai.tool.call.id"] == "call_1"


def test_only_llm_spans_carry_llm_fields():
    attrs = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.request.model": "claude-sonnet-5-5",
        "gen_ai.usage.input_tokens": 5000,
        "gen_ai.usage.output_tokens": 400,
    }
    s = translate(otel(attributes=attrs))
    assert s.kind == SpanKind.AGENT
    assert (s.model, s.input_tokens, s.output_tokens) == (None, None, None)


@pytest.mark.parametrize(
    ("operation", "kind"),
    [
        ("chat", SpanKind.LLM),
        ("text_completion", SpanKind.LLM),
        ("generate_content", SpanKind.LLM),
        ("embeddings", SpanKind.LLM),
        ("execute_tool", SpanKind.TOOL),
        ("invoke_agent", SpanKind.AGENT),
        ("create_agent", SpanKind.AGENT),
        ("invoke_workflow", SpanKind.AGENT),
        ("plan", SpanKind.AGENT),
        ("retrieval", SpanKind.RETRIEVAL),
        ("search_memory", SpanKind.CUSTOM),
        ("fetch_response", SpanKind.CUSTOM),
    ],
)
def test_gen_ai_operations_map_to_kinds(operation, kind):
    s = translate(otel(attributes={"gen_ai.operation.name": operation}))
    assert s.kind == kind
    assert s.attributes["agenttrace.convention"] == "gen_ai"


@pytest.mark.parametrize(
    ("oi_kind", "kind"),
    [
        ("LLM", SpanKind.LLM),
        ("EMBEDDING", SpanKind.LLM),
        ("TOOL", SpanKind.TOOL),
        ("AGENT", SpanKind.AGENT),
        ("RETRIEVER", SpanKind.RETRIEVAL),
        ("RERANKER", SpanKind.RETRIEVAL),
        # LangChain wraps nearly every step in a chain; calling those agents buries the agent.
        ("CHAIN", SpanKind.CUSTOM),
        ("GUARDRAIL", SpanKind.CUSTOM),
        ("EVALUATOR", SpanKind.CUSTOM),
        ("PROMPT", SpanKind.CUSTOM),
        ("DECISION", SpanKind.CUSTOM),
        ("SOMETHING_NEW", SpanKind.CUSTOM),
    ],
)
def test_openinference_kinds_map_to_kinds(oi_kind, kind):
    s = translate(otel(attributes={"openinference.span.kind": oi_kind}))
    assert s.kind == kind
    assert s.attributes["agenttrace.convention"] == "openinference"


def test_openinference_wins_over_gen_ai_on_the_same_span():
    attrs = {"openinference.span.kind": "CHAIN", "gen_ai.operation.name": "chat"}
    s = translate(otel(attributes=attrs))
    assert s.kind == SpanKind.CUSTOM
    assert s.attributes["agenttrace.convention"] == "openinference"


def test_an_openinference_llm_span():
    s = translate(otel(attributes=OPENINFERENCE_LLM))
    assert s.kind == SpanKind.LLM
    assert s.model == "gpt-5.1"
    assert (s.input_tokens, s.output_tokens, s.cache_read_tokens) == (100, 20, 800)
    assert s.input == {"messages": [{"role": "user", "content": "hi"}]}
    assert s.output == "hello"  # text/plain stays text


def test_an_openinference_tool_span():
    s = translate(otel(name="lookup_order_tool", attributes=OPENINFERENCE_TOOL))
    assert s.kind == SpanKind.TOOL
    assert s.name == "lookup_order"
    assert s.input == {"order_id": "42"}
    assert s.output == "shipped"  # no mime type: kept as text unless it's JSON


def test_a_span_without_a_convention_is_custom_with_its_attributes():
    attrs = {"http.request.method": "POST", "http.response.status_code": 200, "url.path": "/ask"}
    s = translate(otel(name="POST /ask", attributes=attrs))
    assert s.kind == SpanKind.CUSTOM
    assert s.attributes == {
        "http.request.method": "POST",
        "http.response.status_code": "200",
        "url.path": "/ask",
        "agenttrace.convention": "none",
    }


def test_vendor_namespaces_are_kept_but_not_mapped():
    attrs = {"traceloop.span.kind": "tool", "traceloop.entity.name": "lookup"}
    s = translate(otel(attributes=attrs))
    assert s.kind == SpanKind.CUSTOM
    assert s.attributes["traceloop.entity.name"] == "lookup"


def test_attribute_values_become_strings_cut_at_4_kb():
    attrs = {
        "flag": True,
        "list": ["a", "b"],
        "big": "é" * 3000,  # 6000 bytes
        "raw": b"\x01\x02",
    }
    a = translate(otel(attributes=attrs)).attributes
    assert a["flag"] == "true"
    assert a["list"] == '["a", "b"]'
    assert len(a["big"].encode()) <= 4096 and a["big"].startswith("éé")
    assert a["raw"] == "0102"


def test_root_span_trace_metadata_from_both_conventions():
    attrs = {
        "gen_ai.conversation.id": "conv-1",
        "enduser.id": "user-7",
        "tag.tags": ["prod", "beta"],
        "metadata": json.dumps({"region": "eu", "attempt": 2}),
    }
    s = translate(otel(parent_span_id=None, attributes=attrs))
    assert s.trace is not None
    assert s.trace.session_id == "conv-1"
    assert s.trace.user_id == "user-7"
    assert s.trace.tags == ["prod", "beta"]
    assert s.trace.metadata == {"region": "eu", "attempt": "2"}


def test_openinference_session_and_user_ids():
    s = translate(otel(parent_span_id=None, attributes={"session.id": "s1", "user.id": "u1"}))
    assert s.trace is not None and (s.trace.session_id, s.trace.user_id) == ("s1", "u1")


def test_trace_metadata_on_a_child_span_stays_an_attribute():
    s = translate(otel(attributes={"session.id": "s1"}))
    assert s.trace is None
    assert s.attributes["session.id"] == "s1"


def test_a_root_span_without_metadata_has_no_trace_metadata():
    assert translate(otel(parent_span_id=None)).trace is None


def test_resource_attributes_go_on_root_spans_only():
    resource = {"service.name": "support-bot", "service.version": "1.4.0"}
    root = translate(otel(parent_span_id=None, resource=resource))
    child = translate(otel(resource=resource))
    assert root.attributes["resource.service.name"] == "support-bot"
    assert root.attributes["resource.service.version"] == "1.4.0"
    assert not any(k.startswith("resource.") for k in child.attributes)


def test_a_span_with_a_remote_parent_is_stored_as_a_root():
    s = translate(otel(parent_is_remote=True, resource={"service.name": "billing"}))
    assert s.parent_span_id is None
    assert s.attributes["resource.service.name"] == "billing"


def test_an_all_zero_parent_means_no_parent():
    assert translate(otel(parent_span_id="0" * 16)).parent_span_id is None


@pytest.mark.parametrize("status", ["unset", "ok"])
def test_unset_and_ok_statuses_are_ok(status):
    assert translate(otel(status=status)).status == SpanStatus.OK


def test_error_type_comes_from_the_error_type_attribute():
    s = translate(otel(status="error", attributes={"error.type": "TimeoutError"}))
    assert s.status == SpanStatus.ERROR and s.error_type == "TimeoutError"


def test_error_type_falls_back_to_the_first_exception_event():
    events = [
        OtelEvent("retry", {}),
        OtelEvent("exception", {"exception.type": "KeyError", "exception.message": "x"}),
        OtelEvent("exception", {"exception.type": "ValueError"}),
    ]
    s = translate(otel(status="error", events=events))
    assert s.error_type == "KeyError"


def test_an_unended_span_has_no_end_time():
    assert translate(otel(end_time_ns=0)).end_time is None


def test_a_span_with_no_name_gets_one():
    assert translate(otel(name="")).name == "span"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"trace_id": "0" * 32}, "trace ID"),
        ({"trace_id": "abc"}, "trace ID"),
        ({"span_id": "0" * 16}, "span ID"),
        ({"span_id": "not-hex-not-hex!"}, "span ID"),
        ({"start_time_ns": 0}, "start time"),
        ({"end_time_ns": T0_NS - 1}, "before"),
    ],
)
def test_spans_that_cant_be_stored_are_refused_with_a_reason(overrides, reason):
    with pytest.raises(Unstorable, match=reason):
        translate(otel(**overrides))


def test_uppercase_ids_are_normalized():
    s = translate(otel(trace_id=TRACE.upper(), span_id=CHILD.upper(), parent_span_id=ROOT.upper()))
    assert (s.trace_id, s.span_id, s.parent_span_id) == (TRACE, CHILD, ROOT)
