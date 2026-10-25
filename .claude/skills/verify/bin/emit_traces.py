"""Emit synthetic traces through the real AgentTrace SDK, exporter, and ingest API.

Each scenario covers a case the demo agent won't reliably hit. Prints one JSON line per scenario
with its trace_id, so the caller can look each trace up in the UI and ClickHouse.
Reads AGENTTRACE_ENDPOINT and AGENTTRACE_API_KEY from the environment (set by agenttrace-verify).
"""

import json
from datetime import UTC, datetime, timedelta

import agenttrace
from agenttrace import Span, SpanKind, new_span_id, new_trace_id, span
from agenttrace.exporter import get_exporter

results: dict[str, str] = {}


def scenario(name: str, trace_id: str) -> None:
    results[name] = trace_id


# 1. A nested agent run: an LLM call priced from the price table, a tool error the agent
#    recovers from (trace stays ok), and trace metadata on the root.
with span(
    "verify.nested", "agent", input={"question": "q1"}, tags=["verify"], user_id="verify-user"
) as root:
    with span("anthropic.messages.create", "llm", input={"messages": ["hi"]}) as llm:
        llm.set_llm("claude-opus-5-5", input_tokens=1000, output_tokens=500)  # $0.004 + $0.01
        llm.set_output([{"type": "text", "text": "calling a tool"}])
    with span("lookup_order", "tool", input={"order_id": "9999"}) as tool:
        tool.set_output("No order with id '9999'")
        tool.set_error("ToolError")
    with span("search_kb", "retrieval", input={"query": "refund"}) as r:
        r.set_output(["policy"])
    root.set_output({"answer": "done"})
scenario("nested", root.trace_id)

# 2. A payload over 8 KB is offloaded to S3 and keeps a 1 KB preview (ADR-0009).
big = "x" * 20_000
with span("verify.large-payload", "agent", input={"blob": big}) as s:
    s.set_output("small output")
scenario("large_payload", s.trace_id)

# 3. A model missing from the price table gets a null cost, not 0 (ADR-0010).
with span("verify.unpriced", "agent") as root, span("llm-call", "llm") as llm:
    llm.set_llm("not-a-real-model", input_tokens=10, output_tokens=10)
scenario("unpriced", root.trace_id)

# 4. An exception in the root marks the trace error.
try:
    with span("verify.root-error", "agent") as root:
        raise RuntimeError("agent crashed")
except RuntimeError:
    pass
scenario("root_error", root.trace_id)

# 5. Prompt-cache tokens are priced on top of input tokens: claude-opus-5-5 at $4 in, $20 out,
#    $0.20 cache read, $5 cache write (5 min): 0.004 + 0.01 + 0.02 + 0.05 = $0.084.
with span("verify.cached", "agent") as root, span("llm-call", "llm") as llm:
    llm.set_llm(
        "claude-opus-5-5",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=100_000,
        cache_write_5m_tokens=10_000,
    )
scenario("cached", root.trace_id)

# 6. A span over the 4 MB request budget is sent truncated and marked, not dropped.
with span("verify.oversized", "agent", input={"image": "A" * 6_000_000}) as s:
    s.set_output("ok")
scenario("oversized", s.trace_id)

# 7. A burst bigger than one request (99 LLM spans with 60 KB conversations, about 6 MB) is
#    split by bytes, so every span arrives.
with span("verify.burst", "agent") as root:
    for _ in range(99):
        with span("anthropic.messages.create", "llm", input={"messages": "m" * 60_000}):
            pass
scenario("burst", root.trace_id)

agenttrace.flush()

# 8. The same span sent three times (in one batch and in a later batch) is stored once (ADR-0014).
exporter = get_exporter()
assert exporter is not None, "AGENTTRACE_API_KEY must be set"
now = datetime.now(UTC)
dup = Span(
    trace_id=new_trace_id(),
    span_id=new_span_id(),
    name="verify.duplicate",
    kind=SpanKind.CUSTOM,
    start_time=now,
    end_time=now + timedelta(milliseconds=5),
)
exporter.export(dup)
exporter.export(dup)
exporter.flush()
exporter.export(dup)
exporter.flush()
scenario("duplicate", dup.trace_id)

# 9. A crashed agent: child spans arrive, the root never does. The trace shows `running`, then
#    `error` once the quiet period passes (ADR-0004, ADR-0013).
crashed = new_trace_id()
exporter.export(
    Span(
        trace_id=crashed,
        span_id=new_span_id(),
        parent_span_id=new_span_id(),  # the root that never ended
        name="verify.orphan-tool",
        kind=SpanKind.TOOL,
        start_time=now,
        end_time=now + timedelta(milliseconds=50),
    )
)
exporter.flush()
scenario("unclosed_root", crashed)

for name, trace_id in results.items():
    print(json.dumps({"scenario": name, "trace_id": trace_id}))
