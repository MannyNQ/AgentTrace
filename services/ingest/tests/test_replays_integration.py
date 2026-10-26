"""Replays against the compose stack: the ClickHouse migration, `replay_of` on `traces`, the
trace input endpoint, and `/v1/replays` in Postgres (ADR-0028, ADR-0029)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from stack import emit_root

from agenttrace_ingest.app import create_app
from agenttrace_ingest.migrate import migrate_clickhouse
from agenttrace_ingest.settings import Settings
from agenttrace_ingest.spans import register_key
from agenttrace_ingest.stores import clickhouse_client
from agenttrace_ingest.trace_inputs import REPLAY_OF_ATTRIBUTE, TRUNCATED_ATTRIBUTE

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def api():
    s = Settings()
    key = f"test-{uuid.uuid4()}"
    with TestClient(create_app(s)) as c:  # lifespan runs both migration series
        register_key(s, key)
        c.headers["Authorization"] = f"Bearer {key}"
        yield c


def test_clickhouse_migrations_are_recorded_and_apply_once(api: TestClient):
    s = Settings()
    ch = clickhouse_client(s)
    names = [r[0] for r in ch.query("SELECT name FROM schema_migrations").result_rows]
    assert "0001_replay_of.sql" in names
    assert migrate_clickhouse(s.postgres_dsn, ch) == []


def test_a_replay_run_s_trace_summary_names_its_original(api: TestClient):
    original = emit_root({"question": "q"})
    run = emit_root({"question": "q"}, attributes={REPLAY_OF_ATTRIBUTE: f"{original}:{'1' * 16}"})
    rows = (
        clickhouse_client(Settings())
        .query(
            """SELECT toString(trace_id), max(replay_of) FROM traces
           WHERE trace_id IN {ids:Array(FixedString(32))} GROUP BY trace_id""",
            parameters={"ids": [original, run]},
        )
        .result_rows
    )
    assert dict(rows) == {original: "", run: original}


def test_trace_input_reads_offloaded_input_and_refuses_a_truncated_one(api: TestClient):
    big = {"question": "x" * 20_000}  # over the 8 KB offload threshold (ADR-0009)
    trace = emit_root(big)
    got = api.get(f"/v1/traces/{trace}/input").json()
    assert got["input"] == big and got["replay_of"] is None and len(got["span_id"]) == 16

    cut = emit_root({"question": "q"}, attributes={TRUNCATED_ATTRIBUTE: "input"})
    assert api.get(f"/v1/traces/{cut}/input").status_code == 422
    assert api.get(f"/v1/traces/{'0' * 32}/input").status_code == 404

    run = emit_root(big, attributes={REPLAY_OF_ATTRIBUTE: f"{trace}:{got['span_id']}"})
    ref = api.get(f"/v1/traces/{run}/input").json()["replay_of"]
    assert ref == {"trace_id": trace, "span_id": got["span_id"]}


def test_a_scored_replay_runs_start_to_finish_in_postgres(api: TestClient):
    trace = emit_root({"question": "refund order 42"})
    root = api.get(f"/v1/traces/{trace}/input").json()["span_id"]
    name = f"it-{uuid.uuid4().hex[:8]}"
    ds = api.post("/v1/datasets", json={"name": name, "trace_id": trace}).json()["id"]
    [item] = api.get(f"/v1/datasets/{ds}/items").json()["items"]
    contents = {**item["contents"], "expected_trajectory": [{"tool": "escalate"}]}
    edit = {"rev": item["rev"], "contents": contents}
    rev = api.patch(f"/v1/datasets/{ds}/items/{item['item_id']}", json=edit).json()["rev"]

    replay = str(uuid.uuid4())
    body = {
        "id": replay,
        "replay_of": {"trace_id": trace, "span_id": root},
        "runs": 2,
        "item_id": item["item_id"],
        "item_rev": rev,
        "scorers": [{"name": "steps", "type": "trajectory", "config": {"mode": "exact"}}],
        "config": {"entry": "demo_agent.evals:eval_entry"},
        "config_hash": "0" * 64,
        "git_commit": "abc123",
        "git_dirty": True,
    }
    assert api.post("/v1/replays", json=body).status_code == 201
    assert api.post("/v1/replays", json=body).status_code == 200

    def run(index: int, score: float) -> dict:
        return {
            "run_index": index,
            "trace_id": uuid.uuid4().hex,
            "started_at": "2026-10-01T00:00:00Z",
            "latency_ms": 10.0,
            "output": "escalated",
            "trajectory": [{"tool": "escalate", "args": {"order_id": "42"}}],
            "usage": [{"model": "claude-haiku-5-5", "input_tokens": 1_000_000}],
            "scores": [{"scorer": "steps", "score": score, "reason": None}],
        }

    batch = {"runs": [run(0, 1.0), run(1, 0.0)]}
    for _ in range(2):  # a retried upload replaces, it doesn't duplicate
        r = api.post(f"/v1/replays/{replay}/runs", json=batch)
        assert r.status_code == 200, r.text
    r = api.post(f"/v1/replays/{replay}/finish", json={"status": "completed"})
    assert r.status_code == 200, r.text

    detail = api.get(f"/v1/replays/{replay}").json()
    assert (detail["status"], detail["runs_uploaded"], detail["git_dirty"]) == (
        "completed",
        2,
        True,
    )
    assert detail["pass_counts"] == [{"scorer": "steps", "passed": 1, "scored": 2}]
    assert detail["cost_usd"] == pytest.approx(2 * 0.10)  # Haiku 5.5 at $0.10/MTok input
    assert detail["item"]["contents"]["expected_trajectory"] == [{"tool": "escalate"}]
    assert [r["run_index"] for r in detail["replay_runs"]] == [0, 1]
    assert detail["replay_runs"][0]["trajectory"] == [
        {"tool": "escalate", "args": {"order_id": "42"}}
    ]

    listed = api.get(f"/v1/replays?trace_id={trace}").json()["replays"]
    assert [x["id"] for x in listed] == [replay]
    assert api.post(f"/v1/replays/{replay}/runs", json=batch).status_code == 409


def test_an_unscored_replay_needs_no_item(api: TestClient):
    trace = emit_root({"question": "hi"})
    replay = str(uuid.uuid4())
    body = {"id": replay, "replay_of": {"trace_id": trace, "span_id": "1" * 16}, "runs": 1}
    assert api.post("/v1/replays", json=body).status_code == 201
    run = {"run_index": 0, "started_at": "2026-10-01T00:00:00Z", "latency_ms": 1.0}
    assert api.post(f"/v1/replays/{replay}/runs", json={"runs": [run]}).status_code == 200
    done = api.post(f"/v1/replays/{replay}/finish", json={"status": "completed"}).json()
    assert (done["status"], done["item_id"], done["pass_counts"]) == ("completed", None, [])
