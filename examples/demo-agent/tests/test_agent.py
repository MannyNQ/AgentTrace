"""Drives the agent loop with a scripted fake client, so no API key or network is needed."""

from types import SimpleNamespace as NS

import pytest

from demo_agent.agent import AgentError, run


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


def tool_use(id_, name, args):
    return NS(type="tool_use", id=id_, name=name, input=args)


def text(t):
    return NS(type="text", text=t)


def test_calls_tool_then_answers():
    client = FakeClient(
        [
            NS(
                stop_reason="tool_use",
                content=[tool_use("t1", "lookup_order", {"order_id": "1042"})],
            ),
            NS(
                stop_reason="end_turn", content=[text("Order 1042 ships via UPS, arriving Oct 28.")]
            ),
        ]
    )
    result = run("Where is order 1042?", client=client)

    assert result.answer.startswith("Order 1042")
    assert [c.name for c in result.tool_calls] == ["lookup_order"]
    # messages: user question, assistant tool_use, user tool_result, assistant answer
    tool_result = client.requests[1]["messages"][2]["content"][0]
    assert tool_result["tool_use_id"] == "t1" and tool_result["is_error"] is False


def test_tool_error_is_returned_to_model():
    client = FakeClient(
        [
            NS(stop_reason="tool_use", content=[tool_use("t1", "lookup_order", {"order_id": "0"})]),
            NS(stop_reason="end_turn", content=[text("I couldn't find that order.")]),
        ]
    )
    result = run("Where is order 0?", client=client)
    assert result.tool_calls[0].is_error


def test_refusal_raises():
    client = FakeClient([NS(stop_reason="refusal", stop_details="cyber", content=[])])
    with pytest.raises(AgentError, match="declined"):
        run("...", client=client)


def test_run_is_traced_as_an_agent_span_with_tool_spans(monkeypatch):
    from agenttrace import tracing

    spans = []
    monkeypatch.setattr(tracing, "get_exporter", lambda: NS(export=spans.append))
    client = FakeClient(
        [
            NS(stop_reason="tool_use", content=[tool_use("t1", "lookup_order", {"order_id": "0"})]),
            NS(stop_reason="end_turn", content=[text("I couldn't find that order.")]),
        ]
    )
    run("Where is order 0?", client=client)

    tool, agent = spans
    assert agent.name == "support_agent" and agent.kind == "agent" and agent.is_root
    assert agent.trace is not None and agent.trace.tags == ["demo-agent"]
    assert agent.input["question"] == "Where is order 0?"
    assert agent.status == "ok"  # the agent recovered from the tool error
    assert tool.name == "lookup_order" and tool.kind == "tool"
    assert tool.parent_span_id == agent.span_id
    assert tool.input == {"order_id": "0"} and tool.status == "error"
