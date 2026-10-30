"""The trace filter against the compose stack: `filter_sql` must select what
`trace_filter.matches` means, which the unit tests pin with the in-memory store (ADR-0038). Also
the ClickHouse migration that adds `service_name` to `traces`."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from stack import emit_spans, register_key

from trayce import Span, new_span_id, new_trace_id
from trayce.eval.results import EVAL_TAG
from trayce.spans import TraceMetadata
from trayce_ingest.app import create_app
from trayce_ingest.migrate import migrate_clickhouse
from trayce_ingest.settings import Settings
from trayce_ingest.stores import clickhouse_client

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def api():
    s = Settings()
    key = f"test-{uuid.uuid4()}"
    with TestClient(create_app(s)) as c:
        # The lifespan applies ClickHouse migrations in the background; don't race it.
        migrate_clickhouse(s.postgres_dsn, clickhouse_client(s))
        register_key(s, key)
        c.headers["Authorization"] = f"Bearer {key}"
        yield c


def trace(
    start: datetime,
    *,
    tags: list[str],
    ended: bool = True,
    error: bool = False,
    user_id: str | None = None,
    attributes: dict[str, str] | None = None,
    children: list[dict] = (),
) -> tuple[str, list[Span]]:
    trace_id, root_id = new_trace_id(), new_span_id()
    root = Span(
        trace_id=trace_id,
        span_id=root_id,
        name="filter.root",
        kind="agent",
        start_time=start,
        end_time=start + timedelta(milliseconds=50) if ended else None,
        status="error" if error else "ok",
        attributes=attributes or {},
        trace=TraceMetadata(tags=tags, user_id=user_id),
    )
    kids = [
        Span(
            trace_id=trace_id,
            span_id=new_span_id(),
            parent_span_id=root_id,
            start_time=start + timedelta(milliseconds=10),
            end_time=start + timedelta(milliseconds=20),
            **c,
        )
        for c in children
    ]
    return trace_id, [root, *kids]


def test_the_filter_sql_selects_what_the_filter_means(api: TestClient):
    # Every case also filters on this tag, so other tests' traces don't count.
    run = f"run-{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)
    since = (now - timedelta(minutes=1)).isoformat()
    refund = {"name": "refund", "kind": "tool"}
    call = {"name": "call", "kind": "llm", "model": "it-model"}
    made = {
        "ok": trace(now, tags=[run, "vip"], user_id="u1",
                    attributes={"resource.service.name": "support"}, children=[refund, call]),
        "error": trace(now, tags=[run], error=True, children=[{**refund, "status": "error"}]),
        "running": trace(now, tags=[run], ended=False),
        "eval": trace(now, tags=[run, EVAL_TAG]),
        "replay": trace(now, tags=[run], attributes={"trayce.replay_of": f"{'a' * 32}:{'1' * 16}"}),  # noqa: E501
        "old": trace(now - timedelta(hours=2), tags=[run]),
    }  # fmt: skip
    emit_spans([s for _, spans in made.values() for s in spans])
    name = {t: n for n, (t, _) in made.items()}
    cases = [
        ({}, {"ok", "error", "running", "old"}),
        ({"since": since}, {"ok", "error", "running"}),
        ({"until": since}, {"old"}),
        ({"show_evals": True, "show_replays": True}, set(name.values())),
        ({"status": "ok"}, {"ok", "old"}),
        ({"status": "error"}, {"error"}),
        ({"status": "running"}, {"running"}),
        ({"tag": [run, "vip"]}, {"ok"}),
        ({"user_id": "u1"}, {"ok"}),
        ({"service_name": "support"}, {"ok"}),
        ({"span_kind": "tool"}, {"ok", "error"}),
        ({"span_name": "refund", "span_error": True, "since": since}, {"error"}),
        ({"span_model": "it-model"}, {"ok"}),
    ]
    for params, want in cases:
        q = {"limit": 200, "tag": [run], **params}  # a case's own tags include `run`
        page = api.get("/v1/traces", params=q)
        assert page.status_code == 200, page.text
        got = {name[t["trace_id"]] for t in page.json()["traces"] if t["trace_id"] in name}
        assert got == want, params
    listed = api.get("/v1/traces", params={"tag": [run], "service_name": "support"}).json()
    assert listed["traces"][0]["service_name"] == "support"
