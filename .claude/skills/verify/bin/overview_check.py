"""Seed traces with known latencies, costs, and errors through the real SDK exporter and an
OpenTelemetry OTLP exporter, then check the overview's numbers against hand-computed values: the
analytics API, the rendered page, and the trace list a tile links to. Every trace carries the
service `overview-verify-<run>`, and every check filters on it, so no other traces count.

    uv run python .claude/skills/verify/bin/overview_check.py <ingest> <web> <full key> <cookie jar>

Prints one PASS or FAIL line per check and exits 1 on any FAIL.
"""

import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace import Status, StatusCode
from web_session import cookie_header

from agenttrace import Span, new_span_id, new_trace_id
from agenttrace.eval.results import EVAL_TAG
from agenttrace.exporter import flush, get_exporter
from agenttrace.spans import TraceMetadata

SERVICE = f"overview-verify-{uuid.uuid4().hex[:6]}"  # its own per run, so reruns don't add up
ingest, web = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/")
key, jar_path = sys.argv[3], sys.argv[4]
failures = 0


def check(name: str, ok: bool, detail: object) -> None:
    global failures
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")


# Through the SDK's exporter, as `@trace` and `span()` send them; backdated ten minutes so every
# trace sits in the current 24h window whatever the hour.
now = datetime.now(UTC) - timedelta(minutes=10)


def sdk_trace(ms: int, *, error=False, tags=(), children=()) -> None:
    tid, root = new_trace_id(), new_span_id()
    spans = [
        Span(trace_id=tid, span_id=root, name="overview.sdk", kind="agent", start_time=now,
             end_time=now + timedelta(milliseconds=ms), status="error" if error else "ok",
             attributes={"resource.service.name": SERVICE},
             trace=TraceMetadata(tags=list(tags), user_id="ov-user"))
    ]  # fmt: skip
    for name, kind, child_ms, fields in children:
        begin = now + timedelta(milliseconds=1)
        spans.append(Span(trace_id=tid, span_id=new_span_id(), parent_span_id=root, name=name,
                          kind=kind, start_time=begin,
                          end_time=begin + timedelta(milliseconds=child_ms), **fields))  # fmt: skip
    exporter = get_exporter()
    assert exporter, "set AGENTTRACE_API_KEY and AGENTTRACE_ENDPOINT"
    for s in spans:
        exporter.export(s)


# A: ok, 100 ms; claude-opus-5-5 at $4/$20 per Mtok: 1000 in + 500 out = $0.014.
sdk_trace(100, children=[
    ("anthropic.messages.create", "llm", 50,
     {"model": "claude-opus-5-5", "input_tokens": 1000, "output_tokens": 500}),
    ("lookup_order", "tool", 20, {}),
])  # fmt: skip
# B: error, 300 ms; its refund tool failed.
sdk_trace(300, error=True, children=[
    ("refund", "tool", 40, {"status": "error", "error_type": "RefundFailed"}),
])  # fmt: skip
# An eval run: left out unless eval and replay runs are included.
sdk_trace(150, tags=[EVAL_TAG])
flush()

# Through OpenTelemetry's own SDK and OTLP/HTTP exporter, GenAI conventions.
provider = TracerProvider(resource=Resource.create({"service.name": SERVICE}))
provider.add_span_processor(
    SimpleSpanProcessor(
        OTLPSpanExporter(
            endpoint=f"{ingest}/otlp/v1/traces", headers={"Authorization": f"Bearer {key}"}
        )
    )
)
tracer = provider.get_tracer("overview-verify")
ns = lambda t, ms=0: int((t + timedelta(milliseconds=ms)).timestamp() * 1e9)  # noqa: E731


def otel_trace(ms: int, model: str, tokens: dict, tool: str | None) -> None:
    root = tracer.start_span("invoke_agent support", start_time=ns(now),
                             attributes={"gen_ai.operation.name": "invoke_agent"})  # fmt: skip
    from opentelemetry import trace as ot

    ctx = ot.set_span_in_context(root)
    chat = tracer.start_span(f"chat {model}", context=ctx, start_time=ns(now, 1), attributes={
        "gen_ai.operation.name": "chat", "gen_ai.request.model": model,
        "gen_ai.response.model": model, **tokens})  # fmt: skip
    chat.end(end_time=ns(now, 40))
    if tool:
        t = tracer.start_span(f"execute_tool {tool}", context=ctx, start_time=ns(now, 41),
                              attributes={"gen_ai.operation.name": "execute_tool",
                                          "gen_ai.tool.name": tool})  # fmt: skip
        t.set_status(Status(StatusCode.OK))
        t.end(end_time=ns(now, 71))
    root.end(end_time=ns(now, ms))


# D: ok, 200 ms; claude-sonnet-5-5 at $2/$10 and $0.20 cached per Mtok: 1500 input of which 1000
# cached, 40 output = 500 * 2 + 1000 * 0.2 + 40 * 10 = $0.0016.
otel_trace(200, "claude-sonnet-5-5", {"gen_ai.usage.input_tokens": 1500,
           "gen_ai.usage.output_tokens": 40, "gen_ai.usage.cache_read.input_tokens": 1000},
           "search_kb")  # fmt: skip
# E: ok, 400 ms; a model with no price.
otel_trace(400, "not-a-real-model", {"gen_ai.usage.input_tokens": 10,
           "gen_ai.usage.output_tokens": 10}, None)  # fmt: skip
provider.shutdown()

COST = 0.014 + 0.0016


def api(part: str, **params) -> dict:
    q = urllib.parse.urlencode({"service_name": SERVICE, "range": "24h", **params})
    req = urllib.request.Request(f"{ingest}/v1/analytics/{part}?{q}",
                                 headers={"Authorization": f"Bearer {key}"})  # fmt: skip
    return json.load(urllib.request.urlopen(req))


# The worker stores spans a moment after ingest queues them.
for _ in range(30):
    if api("volume")["totals"]["traces"] >= 4:
        break
    time.sleep(1)

v = api("volume")
check("volume totals", v["totals"] == {"traces": 4, "running": 0, "completed": 4, "errors": 1,
      "error_rate": 0.25}, v["totals"])  # fmt: skip
kinds = {k["kind"]: (k["spans"], k["errors"]) for k in v["span_kinds"]}
check("span kinds", kinds == {"agent": (4, 1), "llm": (3, 0), "tool": (3, 1)}, kinds)
check("eval runs included on request", api("volume", show_evals="true")["totals"]["traces"] == 5,
      "5 with show_evals")  # fmt: skip
lat = api("latency")["totals"]
# t-digest percentiles are approximate (ADR-0037): 400 ms can read 400.001.
check("latency", lat["completed"] == 4 and 199 <= lat["p50"] <= 301 and lat["p99"] <= 401, lat)
c = api("cost")
check("cost", abs(c["totals"]["cost"] - COST) < 1e-9 and c["totals"]["unpriced_llm_spans"] == 1
      and abs(c["totals"]["cost_per_trace"] - COST / 4) < 1e-9, c["totals"])  # fmt: skip
models = {m["model"]: (m["calls"], m["unpriced_calls"], round(m["cost"], 6)) for m in c["models"]}
check("models", models == {"claude-opus-5-5": (1, 0, 0.014), "claude-sonnet-5-5": (1, 0, 0.0016),
      "not-a-real-model": (1, 1, 0.0)}, models)  # fmt: skip
tools = {g["name"]: (g["calls"], g["errors"]) for g in api("tools")["groups"]}
check("tools", tools == {"lookup_order": (1, 0), "refund": (1, 1), "search_kb": (1, 0)}, tools)

# The page, as the signed-in owner sees it.
cookies = cookie_header(jar_path)


def page(path: str) -> str:
    req = urllib.request.Request(f"{web}{path}", headers={"Cookie": cookies})
    return urllib.request.urlopen(req).read().decode()


overview = page(f"/p/default/overview?service={SERVICE}")
tiles = dict(re.findall(r'data-tile="([^"]+)".*?data-tile-value="?[^>]*>([^<]+)<', overview, re.S))
expected = {"Traces": "4", "Error rate": "25%", "Cost per trace": "$0.0039"}
check("tiles", all(tiles.get(k, "").replace("≥ ", "") == v for k, v in expected.items()), tiles)
check("unpriced note", "not-a-real-model" in overview and "has no price" in overview, "listed")
link = re.search(r'data-tile="Error rate".*?href="([^"]+)"', overview, re.S)
listed = page(html.unescape(link.group(1))) if link else ""
rows = re.findall(r'data-trace-id="([0-9a-f]{32})"', listed)
check("error tile opens its traces", len(rows) == 1 and "status error" in listed,
      f"{len(rows)} row(s) at {link and html.unescape(link.group(1))}")  # fmt: skip

sys.exit(1 if failures else 0)
