import contextvars
import dataclasses
import json
import threading

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace import Status, StatusCode
from test_eval_runner import SUPPORT, FakeApi

from agenttrace import SpanKind, SpanStatus, span, tracing
from agenttrace.eval import config, scorers
from agenttrace.eval.runner import Git, Runner
from agenttrace.otel import bridge
from agenttrace.tracing import untraced

TOOL = {
    "gen_ai.operation.name": "execute_tool",
    "gen_ai.tool.name": "lookup_order",
    "gen_ai.tool.call.arguments": json.dumps({"order_id": "1042"}),
}
CHAT = {
    "gen_ai.operation.name": "chat",
    "gen_ai.request.model": "claude-haiku-5-5",
    "gen_ai.usage.input_tokens": 100,
    "gen_ai.usage.output_tokens": 10,
}


@pytest.fixture(autouse=True)
def own_context_after(monkeypatch):
    """Each test starts, and leaves the rest of the suite, with the SDK on its own context."""
    monkeypatch.setattr(tracing, "_otel", None)


@pytest.fixture
def tracer():
    provider = TracerProvider()
    bridge.install(provider)
    return provider.get_tracer("test")


def test_an_otel_span_inside_an_agenttrace_span_joins_its_trace(collected, tracer):
    with (
        span("agent", "agent") as root,
        tracer.start_as_current_span("execute_tool", attributes=TOOL),
    ):
        pass
    tool = collected.by_name("lookup_order")
    assert tool.kind == SpanKind.TOOL
    assert tool.trace_id == root.trace_id and tool.parent_span_id == root.span_id
    assert tool.input == {"order_id": "1042"}
    assert tool.attributes["agenttrace.convention"] == "gen_ai"


def test_an_agenttrace_span_inside_an_otel_span_is_its_child(collected, tracer):
    with (
        tracer.start_as_current_span(
            "invoke_agent", attributes={"gen_ai.operation.name": "invoke_agent"}
        ) as o,
        span("lookup_order", "tool"),
    ):
        pass
    ctx = o.get_span_context()
    inner, outer = collected.by_name("lookup_order"), collected.by_name("invoke_agent")
    assert inner.trace_id == format(ctx.trace_id, "032x") == outer.trace_id
    assert inner.parent_span_id == outer.span_id
    assert outer.parent_span_id is None and outer.kind == SpanKind.AGENT


def test_otel_status_and_error_type_come_through(collected, tracer):
    with span("agent", "agent"), tracer.start_as_current_span("execute_tool", attributes=TOOL) as s:
        s.set_status(Status(StatusCode.ERROR))
        s.set_attribute("error.type", "OrderNotFound")
    tool = collected.by_name("lookup_order")
    assert tool.status == SpanStatus.ERROR and tool.error_type == "OrderNotFound"


def test_otel_spans_reach_span_processors(tracer):
    seen = []
    remove = tracing.add_span_processor(seen.append)
    try:
        with tracer.start_as_current_span("execute_tool", attributes=TOOL):
            pass
    finally:
        remove()
    assert [s.name for s in seen] == ["lookup_order"]


def test_untraced_drops_otel_spans_too(collected, tracer):
    with untraced(), tracer.start_as_current_span("chat", attributes=CHAT):
        pass
    assert collected.spans == []


def test_an_otel_llm_span_under_an_agenttrace_llm_span_is_the_same_call(collected, tracer):
    """The Anthropic SDK records its own GenAI span inside `instrument_anthropic()`'s; keeping
    both would count the call, and its cost, twice."""
    with span("anthropic.messages.create", "llm") as llm:
        llm.set_llm("claude-haiku-5-5", 100, 10)
        with tracer.start_as_current_span("chat claude-haiku-5-5", attributes=CHAT):
            pass
    assert [s.name for s in collected.spans] == ["anthropic.messages.create"]


def test_other_otel_spans_under_an_agenttrace_llm_span_are_kept(collected, tracer):
    with (
        span("anthropic.messages.create", "llm"),
        tracer.start_as_current_span("POST https://api.anthropic.com"),
    ):
        pass
    assert {s.name for s in collected.spans} == {
        "anthropic.messages.create",
        "POST https://api.anthropic.com",
    }


def test_installing_twice_records_each_span_once(collected):
    provider = TracerProvider()
    bridge.install(provider)
    bridge.install(provider)
    with provider.get_tracer("t").start_as_current_span("chat", attributes=CHAT):
        pass
    assert len(collected.spans) == 1


def test_without_the_bridge_the_sdk_ignores_otel_context(collected):
    provider = TracerProvider()  # spans record, but nothing bridges them or shares context
    with provider.get_tracer("t").start_as_current_span("outer"), span("mine", "agent") as mine:
        pass
    assert mine.parent_span_id is None
    assert [s.name for s in collected.spans] == ["mine"]


def test_context_follows_into_a_thread_started_with_a_copied_context(collected, tracer):
    def work():
        with tracer.start_as_current_span("execute_tool", attributes=TOOL):
            pass

    with span("agent", "agent") as root:
        t = threading.Thread(target=contextvars.copy_context().run, args=(work,))
        t.start()
        t.join()
    assert collected.by_name("lookup_order").parent_span_id == root.span_id


def otel_agent(input: dict) -> str:
    """An agent instrumented with OpenTelemetry only, through the global tracer provider."""
    tracer = otel_trace.get_tracer("otel-agent")
    order = input["question"].split()[-1]
    with tracer.start_as_current_span(
        "invoke_agent", attributes={"gen_ai.operation.name": "invoke_agent"}
    ):
        tool = {**TOOL, "gen_ai.tool.call.arguments": json.dumps({"order_id": order})}
        with tracer.start_as_current_span("execute_tool lookup_order", attributes=tool):
            pass
        with tracer.start_as_current_span("chat", attributes=CHAT):
            pass
    return f"Order {order} ships with UPS"


def test_eval_installs_the_bridge_and_sees_an_otel_agents_trajectory(monkeypatch):
    monkeypatch.setattr(tracing, "get_exporter", lambda: None)
    cfg = config.parse({
        "entry": "tests:otel_agent", "dataset": "support", "samples": 2, "concurrency": 4,
        "scorers": [{"name": "steps", "type": "trajectory", "mode": "in_order"}],
    })  # fmt: skip
    version = dataclasses.replace(SUPPORT, items=SUPPORT.items[:1])
    api = FakeApi()
    runner = Runner(cfg, api, otel_agent, scorers.build(cfg), report=lambda _: None)  # type: ignore[arg-type]
    runner.run(version, Git(commit="abc", branch="main"))
    assert len(api.samples) == 2
    for s in api.samples:
        assert [(c.tool, c.args) for c in s.trajectory] == [("lookup_order", {"order_id": "1042"})]
        assert {sc.scorer: sc.score for sc in s.scores} == {"steps": 1}
        assert s.usage[0].model == "claude-haiku-5-5"
    assert isinstance(otel_trace.get_tracer_provider(), TracerProvider)


def test_the_exporters_own_requests_never_become_spans(monkeypatch, http_server):
    """With urllib instrumented, as `opentelemetry-instrument` does, each POST to ingest would be
    an OTel span the bridge exports with the next POST, forever."""
    from opentelemetry.instrumentation.urllib import URLLibInstrumentor

    from agenttrace.eval.client import Client
    from agenttrace.exporter import Config, Exporter

    posts: list[list[str]] = []

    def ingest(path, headers, body):
        if path == "/v1/spans":
            posts.append([s["name"] for s in json.loads(body)["spans"]])
        return 202, {}

    url = http_server(ingest)
    provider = TracerProvider()
    seen = []
    provider.add_span_processor(SimpleSpanProcessor(InMemory(seen)))
    bridge.install(provider)
    exporter = Exporter(Config(api_key="k", endpoint=url, flush_interval_s=0.05))
    monkeypatch.setattr(tracing, "get_exporter", lambda: exporter)
    URLLibInstrumentor().instrument(tracer_provider=provider)
    try:
        with span("agent", "agent"):
            pass
        for _ in range(5):
            exporter.flush()
        Client(url, "k").request("POST", "/v1/experiments", {})  # the eval runner's uploads
        exporter.flush()
    finally:
        URLLibInstrumentor().uninstrument()
    assert posts == [["agent"]]
    assert seen == []  # suppressed for other OTel exporters too, as OTel's own exporters do


class InMemory:
    def __init__(self, into: list):
        self.into = into

    def export(self, spans):
        self.into.extend(spans)
        from opentelemetry.sdk.trace.export import SpanExportResult

        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass


def test_a_span_that_cant_be_translated_never_reaches_the_agents_code(monkeypatch, collected):
    def broken(_):
        raise RuntimeError("translator bug")

    monkeypatch.setattr(bridge, "translate", broken)
    provider = TracerProvider()
    bridge.install(provider)
    with provider.get_tracer("t").start_as_current_span("chat", attributes=CHAT):
        pass  # the agent carries on
    assert collected.spans == []


def test_deeply_nested_json_in_an_attribute_is_kept_as_text(collected, tracer):
    deep = "[" * 100_000
    attrs = {**TOOL, "gen_ai.tool.call.result": deep}
    with tracer.start_as_current_span("execute_tool", attributes=attrs):
        pass
    assert collected.by_name("lookup_order").output == deep
