"""The scripted Pydantic AI agent, and its OpenTelemetry spans as the OTel bridge translates them.
No API key or network: the model is a `FunctionModel`."""

import pytest

from agenttrace import SpanKind, SpanStatus, add_span_processor, otel, span, tracing
from agenttrace.eval.runner import trajectory
from demo_agent_otel.agent import MODEL_NAME, run


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        ("Where is order 1042?", "Order 1042 is shipped, shipping with UPS, expected 2026-10-28."),
        ("I'd like to return order 1043, I changed my mind.", "Refunded $58.00 for order 1043."),
        ("Where is order 9999?", "No order with id '9999'."),
        ("zzz qqq", "A person will follow up on ticket HUMAN-0001."),
    ],
)
def test_the_policy_answers(question, answer):
    assert run(question) == answer


def test_a_refund_outside_the_window_says_why():
    assert "outside the 30-day window" in run("Can I get a refund on order 1044?")


@pytest.fixture
def bridged(monkeypatch):
    monkeypatch.setattr(tracing, "_otel", None)  # leave the rest of the suite on its own context
    monkeypatch.setattr(tracing, "get_exporter", lambda: None)
    otel.install()
    seen = []
    remove = add_span_processor(seen.append)
    yield seen
    remove()


REFUND_1043 = "I'd like to return order 1043, I changed my mind."


def test_its_spans_nest_under_an_agenttrace_span_through_the_bridge(bridged):
    with span("sample", "agent", input={"question": "q"}) as root:
        run(REFUND_1043)
    spans = [s for s in bridged if s.trace_id == root.trace_id and s.span_id != root.span_id]
    by_kind = {k: [s for s in spans if s.kind == k] for k in SpanKind}
    [agent] = by_kind[SpanKind.AGENT]
    assert agent.name == "invoke_agent support" and agent.parent_span_id == root.span_id
    assert [(c.tool, c.args) for c in trajectory(spans)] == [
        ("lookup_order", {"order_id": "1043"}),
        ("issue_refund", {"order_id": "1043", "reason": REFUND_1043}),
    ]
    llm = by_kind[SpanKind.LLM]
    assert len(llm) == 3  # one per model request: two tool calls, then the answer
    assert all(s.model == MODEL_NAME and s.input_tokens and s.output_tokens for s in llm)
    assert all(s.attributes["agenttrace.convention"] == "gen_ai" for s in spans)


def test_a_failed_tool_call_is_an_error_span(bridged):
    with span("sample", "agent"):
        run("Can I get a refund on order 1044?")
    [refund] = [s for s in bridged if s.name == "issue_refund"]
    assert refund.status == SpanStatus.ERROR
