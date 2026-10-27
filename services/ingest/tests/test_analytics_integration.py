"""The overview's queries against the compose stack, in a project of their own, checked against
numbers worked out by hand: volume, running traces, error rates by trace and span kind, latency,
cost with an unpriced call, tools and models, the previous period, and the filter (ADR-0037)."""

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from stack import emit_spans, register_key

from agenttrace import Span, new_span_id, new_trace_id
from agenttrace.eval.results import EVAL_TAG
from agenttrace.pricing import Price, PriceTable
from agenttrace.spans import TraceMetadata
from agenttrace_ingest.app import create_app
from agenttrace_ingest.settings import Settings

pytestmark = pytest.mark.integration

# Per million tokens: input, output, cache read, 5-minute cache write.
PRICES = PriceTable(
    by_model={"it-model": [Price(datetime(2020, 1, 1, tzinfo=UTC), 1.0, 2.0, 0.1, 1.25)]}
)
T1_COST = (1000 * 1.0 + 500 * 2.0 + 200 * 0.1 + 100 * 1.25) / 1e6  # 0.002145
T3_COST = 2000 * 1.0 / 1e6


def trace(start: datetime, ms: int | None, *, error=False, tags=(), children=()) -> list[Span]:
    """A root lasting `ms` (None: still running) and its children, each `(name, kind, ms,
    fields)`, starting 1 ms after the root."""
    trace_id, root = new_trace_id(), new_span_id()
    spans = [
        Span(
            trace_id=trace_id,
            span_id=root,
            name="it.root",
            kind="agent",
            start_time=start,
            end_time=None if ms is None else start + timedelta(milliseconds=ms),
            status="error" if error else "ok",
            trace=TraceMetadata(tags=list(tags)),
        )
    ]
    for name, kind, child_ms, fields in children:
        begin = start + timedelta(milliseconds=1)
        spans.append(
            Span(
                trace_id=trace_id,
                span_id=new_span_id(),
                parent_span_id=root,
                name=name,
                kind=kind,
                start_time=begin,
                end_time=begin + timedelta(milliseconds=child_ms),
                **fields,
            )
        )
    return spans


@pytest.fixture(scope="module")
def api():
    s = Settings()
    org, project = uuid.uuid4(), uuid.uuid4()
    key = f"test-{uuid.uuid4()}"
    with TestClient(create_app(s)) as c:
        with psycopg.connect(s.postgres_dsn) as conn:
            conn.execute("INSERT INTO orgs (id, name) VALUES (%s, %s)", (org, f"it-{org}"))
            conn.execute(
                "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, %s, %s)",
                (project, org, f"it-{org.hex[:8]}", f"it-{org.hex[:8]}"),
            )
        register_key(s, key, project)
        c.headers["Authorization"] = f"Bearer {key}"
        now = datetime.now(UTC) - timedelta(seconds=2)
        llm = "llm"
        spans = [
            *trace(now, 100, tags=["vip"], children=[
                ("call", llm, 50, {"model": "it-model", "input_tokens": 1000, "output_tokens": 500,
                                   "cache_read_tokens": 200, "cache_write_5m_tokens": 100}),
                ("search", "tool", 10, {}),
            ]),
            *trace(now, 300, error=True, children=[
                ("refund", "tool", 20, {"status": "error", "error_type": "ToolError"}),
            ]),
            *trace(now, 200, children=[
                ("call", llm, 40, {"model": "it-model", "input_tokens": 2000, "output_tokens": 0}),
                ("call", llm, 40, {"model": "mystery", "input_tokens": 5, "output_tokens": 5}),
                ("search", "tool", 30, {}),
            ]),
            *trace(now, None),  # running
            *trace(now, 100, tags=[EVAL_TAG], children=[
                ("call", llm, 50, {"model": "it-model", "input_tokens": 1000, "output_tokens": 0}),
            ]),
            *trace(now - timedelta(hours=30), 50),  # the previous period
        ]  # fmt: skip
        emit_spans(spans, project, PRICES)
        yield c


def get(api: TestClient, part: str, **params) -> dict:
    r = api.get(f"/v1/analytics/{part}", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_volume_counts_running_traces_but_rates_only_completed_ones(api: TestClient):
    v = get(api, "volume")
    assert v["totals"] == {
        "traces": 4, "running": 1, "completed": 3, "errors": 1, "error_rate": pytest.approx(1 / 3),
    }  # fmt: skip
    assert v["previous"] == {
        "traces": 1, "running": 0, "completed": 1, "errors": 0, "error_rate": 0.0,
    }  # fmt: skip
    assert len(v["buckets"]) == 24 and sum(b["traces"] for b in v["buckets"]) == 4
    kinds = {k["kind"]: (k["spans"], k["errors"]) for k in v["span_kinds"]}
    assert kinds == {"agent": (4, 1), "tool": (3, 1), "llm": (3, 0)}


def test_latency_is_over_completed_traces(api: TestClient):
    lat = get(api, "latency")
    assert lat["totals"]["completed"] == 3
    assert lat["totals"]["p50"] == pytest.approx(200, abs=1)
    assert 200 <= lat["totals"]["p95"] <= 300 and lat["totals"]["p99"] <= 300
    assert lat["previous"]["p50"] == pytest.approx(50, abs=1)


def test_cost_by_model_keeps_unpriced_calls_apart(api: TestClient):
    c = get(api, "cost")
    assert c["totals"]["cost"] == pytest.approx(T1_COST + T3_COST)
    assert c["totals"]["cost_per_trace"] == pytest.approx((T1_COST + T3_COST) / 3)
    assert c["totals"]["unpriced_llm_spans"] == 1
    assert c["top_models"] == ["it-model"]
    assert sum(b["total"] for b in c["buckets"]) == pytest.approx(T1_COST + T3_COST)
    models = {m["model"]: m for m in c["models"]}
    it = models["it-model"]
    assert (it["calls"], it["unpriced_calls"]) == (2, 0)
    assert (it["input_tokens"], it["output_tokens"]) == (3000, 500)
    assert (it["cache_read_tokens"], it["cache_write_tokens"]) == (200, 100)
    assert it["cost_per_call"] == pytest.approx((T1_COST + T3_COST) / 2)
    assert models["mystery"]["unpriced_calls"] == 1 and models["mystery"]["cost"] == 0


def test_tools_and_models(api: TestClient):
    tools = {g["name"]: g for g in get(api, "tools")["groups"]}
    assert (tools["search"]["calls"], tools["search"]["errors"]) == (2, 0)
    assert tools["refund"]["error_rate"] == 1.0
    assert 10 <= tools["search"]["p95"] <= 30 and not tools["search"]["ranked"]
    models = {g["name"]: g["calls"] for g in get(api, "models")["groups"]}
    assert models == {"it-model": 2, "mystery": 1}


def test_the_filter_and_the_zone_apply(api: TestClient):
    assert get(api, "volume", tag="vip")["totals"]["traces"] == 1
    assert get(api, "volume", show_evals=True)["totals"]["traces"] == 5
    assert get(api, "volume", status="error")["totals"]["traces"] == 1
    assert get(api, "tools", span_name="refund")["groups"][0]["name"] == "refund"
    kolkata = get(api, "volume", tz="Asia/Kolkata")
    assert sum(b["traces"] for b in kolkata["buckets"]) == 4
    assert kolkata["buckets"][-1]["start"].endswith(":30:00Z")
    week = get(api, "volume", range="7d")
    assert len(week["buckets"]) == 7 and week["totals"]["traces"] == 5  # 30 h ago is this week


def test_concurrent_reads_share_one_client(api: TestClient):
    """The overview's panels load in parallel, on threads sharing one store."""
    from concurrent.futures import ThreadPoolExecutor

    from agenttrace_ingest.analytics_store import ClickHouseAnalytics
    from agenttrace_ingest.trace_filter import TraceFilter

    store = ClickHouseAnalytics(Settings())
    now = datetime.now(UTC)
    f = TraceFilter(since=now - timedelta(days=1), until=now)
    with ThreadPoolExecutor(8) as pool:
        rows = list(pool.map(lambda _: store.trace_totals(uuid.uuid4(), f, now), range(16)))
    assert all(r.traces == 0 for r in rows)
