"""Experiments and pinning against the compose stack's Postgres and ClickHouse."""

import time
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from stack import emit_root, register_key

from trayce_ingest.app import create_app
from trayce_ingest.auth import DEFAULT_PROJECT_ID
from trayce_ingest.experiment_store import PostgresExperiments
from trayce_ingest.settings import Settings
from trayce_ingest.stores import clickhouse_client

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def api():
    s = Settings()
    key = f"test-{uuid.uuid4()}"
    with TestClient(create_app(s)) as c:  # lifespan runs the migrations
        register_key(s, key)
        c.headers["Authorization"] = f"Bearer {key}"
        yield c


def published(api: TestClient, *items: dict) -> str:
    ds = api.post("/v1/datasets", json={"name": f"it-{uuid.uuid4().hex[:8]}"}).json()["id"]
    for item in items:
        assert api.post(f"/v1/datasets/{ds}/items", json=item).status_code == 201
    assert api.post(f"/v1/datasets/{ds}/publish").status_code == 200
    return ds


def test_an_experiment_runs_start_to_finish_in_postgres(api: TestClient):
    ds = published(
        api,
        {"input": "q1", "expected_output": "A"},
        {"input": "q2", "expected_trajectory": [{"tool": "lookup_order"}]},
    )
    items = [i["item_id"] for i in api.get(f"/v1/datasets/{ds}/versions/1").json()["items"]]
    exp = str(uuid.uuid4())
    body = {
        "id": exp,
        "dataset_id": ds,
        "dataset_version": 1,
        "samples": 1,
        "scorers": [{"name": "exact", "type": "exact_match"}],
        "config": {"entry": "demo_agent:eval_entry"},
        "config_hash": "0" * 64,
        "git_branch": "phase-3-evals",
    }
    assert api.post("/v1/experiments", json=body).status_code == 201
    assert api.post("/v1/experiments", json=body).status_code == 200

    def sample(item_id: str, score: float) -> dict:
        return {
            "item_id": item_id,
            "sample_index": 0,
            "started_at": "2026-10-01T00:00:00Z",
            "latency_ms": 12.5,
            "output": {"answer": "A"},
            "trajectory": [{"tool": "lookup_order", "args": {"order_id": "1042"}}],
            "usage": [
                {"model": "claude-haiku-5-5", "input_tokens": 1_000_000, "cache_read_tokens": 10}
            ],
            "scores": [{"scorer": "exact", "score": score, "reason": "differs"}],
        }

    batch = {"samples": [sample(items[0], 0.0), sample(items[1], 1.0)]}
    for _ in range(2):  # a retried upload replaces, it doesn't duplicate
        r = api.post(f"/v1/experiments/{exp}/samples", json=batch)
        assert r.status_code == 200, r.text
    r = api.post(f"/v1/experiments/{exp}/finish", json={"status": "completed"})
    assert r.status_code == 200, r.text

    detail = api.get(f"/v1/experiments/{exp}").json()
    assert (detail["status"], detail["samples_uploaded"], detail["item_count"]) == (
        "completed",
        2,
        2,
    )
    # Priced from the seeded table: Haiku 5.5 at $0.10/MTok input plus 10 cache reads.
    assert detail["cost_usd"] == pytest.approx(2 * (0.10 + 10 * 0.01 / 1e6))
    assert detail["scorer_stats"][0]["pass_rate"] == 0.5
    assert detail["git_branch"] == "phase-3-evals"

    assert [i["item_id"] for i in detail["items"]] == items  # in dataset order

    one = api.get(f"/v1/experiments/{exp}/items/{items[0]}").json()
    assert one["item"]["contents"]["expected_output"] == "A"
    [s] = one["samples"]
    assert s["output"] == {"answer": "A"}
    assert s["trajectory"] == [{"tool": "lookup_order", "args": {"order_id": "1042"}}]
    assert s["scores"] == [{"scorer": "exact", "score": 0.0, "passed": False, "reason": "differs"}]

    listed = api.get(f"/v1/experiments?dataset_id={ds}").json()["experiments"]
    assert [e["id"] for e in listed] == [exp]
    late = api.post(f"/v1/experiments/{exp}/samples", json=batch)
    assert late.status_code == 409

    # The store scopes every read by project, so another project's ids read nothing (#32).
    store, other = PostgresExperiments(Settings()), uuid.uuid4()
    exp_id, item_id = uuid.UUID(exp), uuid.UUID(items[0])
    assert len(store.sample_rows(DEFAULT_PROJECT_ID, exp_id)) == 2
    assert store.sample_rows(other, exp_id) == []
    assert store.score_rows(other, exp_id) == []
    assert store.samples(other, exp_id, item_id) == []
    assert store.item(DEFAULT_PROJECT_ID, item_id, 1) is not None
    assert store.item(other, item_id, 1) is None


def test_a_pull_request_run_is_compared_with_its_merge_base_run_in_postgres(api: TestClient):
    ds = published(api, *({"input": f"q{n}", "expected_output": "A"} for n in range(4)))
    items = [i["item_id"] for i in api.get(f"/v1/datasets/{ds}/versions/1").json()["items"]]
    config_hash = uuid.uuid4().hex * 2  # unique to this test, so other runs aren't baselines

    def run(passed: int, **git) -> str:
        """Each item's 3 samples score 1 on the first `passed` items, 0 on the rest."""
        exp = str(uuid.uuid4())
        body = {
            "id": exp, "dataset_id": ds, "dataset_version": 1, "samples": 3,
            "scorers": [{"name": "exact", "type": "exact_match", "max_drop": 0.1}],
            "config": {}, "config_hash": config_hash, **git,
        }  # fmt: skip
        assert api.post("/v1/experiments", json=body).status_code == 201
        samples = [
            {
                "item_id": item, "sample_index": i, "started_at": "2026-10-01T00:00:00Z",
                "latency_ms": 10.0 * (n + 1), "output": "A",
                "judge_usage": [{"model": "claude-haiku-5-5", "input_tokens": 1_000_000}],
                "scores": [{"scorer": "exact", "score": 1.0 if n < passed else 0.0}],
            }
            for n, item in enumerate(items)
            for i in range(3)
        ]  # fmt: skip
        r = api.post(f"/v1/experiments/{exp}/samples", json={"samples": samples})
        assert r.status_code == 200, r.text
        assert api.post(f"/v1/experiments/{exp}/finish", json={"status": "completed"}).is_success
        return exp

    at_merge_base = run(4, git_commit="m1", git_branch="main")
    run(4, git_commit="m2", git_branch="main")
    pr = run(1, git_commit="p1", git_branch="fix", git_merge_base="m1", git_base_branch="main")

    c = api.get(f"/v1/experiments/{pr}/comparison").json()
    assert (c["baseline"]["id"], c["baseline_reason"]) == (at_merge_base, "merge_base")
    [exact] = c["scorers"]
    assert (exact["items"], exact["baseline_rate"], exact["current_rate"]) == (4, 1.0, 0.25)
    assert exact["regressed"] and exact["max_drop"] == 0.1
    assert c["regressions"] == 1 and len(c["item_regressions"]) == 3
    assert c["current_stats"]["p50_latency_ms"] == 25.0
    assert c["experiment"]["git_merge_base"] == "m1"
    assert c["experiment"]["judge_cost_usd"] == pytest.approx(12 * 0.10)

    # Nothing at the merge-base: the newest run on the base branch.
    pr2 = run(4, git_merge_base="gone", git_base_branch="main")
    c = api.get(f"/v1/experiments/{pr2}/comparison").json()
    assert c["baseline_reason"] == "base_branch" and c["regressions"] == 0


def expiry(trace_id: str) -> tuple[list[datetime], list[datetime]]:
    """When each of the trace's rows expires, by the expression both tables' TTL uses."""
    ch = clickhouse_client(Settings())
    params = {"t": trace_id}
    found = []
    for table in ("spans", "traces"):
        rows = ch.query(
            f"SELECT toDateTime(start_time) + toIntervalDay(retention_days) FROM {table} "
            "WHERE trace_id = {t:FixedString(32)}",
            parameters=params,
        ).result_rows
        found.append([r[0].replace(tzinfo=UTC) for r in rows])
    return found[0], found[1]


def wait_for_mutations() -> None:
    ch = clickhouse_client(Settings())
    for _ in range(100):
        if ch.command("SELECT count() FROM system.mutations WHERE NOT is_done") == 0:
            return
        time.sleep(0.1)
    raise AssertionError("mutations still running after 10 s")


def test_adding_a_trace_pins_it(api: TestClient):
    """Two traces with a 1-day retention: the one added to a dataset no longer expires."""
    kept = emit_root({"question": "Where is order 1042?"}, retention_days=1)
    other = emit_root({"question": "unlinked"}, retention_days=1)
    ds = api.post("/v1/datasets", json={"name": f"it-{uuid.uuid4().hex[:8]}"}).json()["id"]
    r = api.post(f"/v1/datasets/{ds}/items/from-trace", json={"trace_id": kept})
    assert r.status_code == 201, r.text
    wait_for_mutations()

    tomorrow = datetime.now(UTC) + timedelta(days=1, minutes=1)
    spans, traces = expiry(kept)
    assert len(spans) == len(traces) == 1
    # Not wrapped around: DateTime ends in 2106, and an overflowing TTL lands back in 1970-2069.
    assert all(t.year == 2106 for t in spans + traces), (spans, traces)
    spans, traces = expiry(other)
    assert all(t < tomorrow for t in spans + traces) and spans and traces
    ch = clickhouse_client(Settings())
    for table in ("spans", "traces"):
        assert "retention_days" in ch.command(f"SHOW CREATE TABLE {table}").split("TTL")[-1]

    with psycopg.connect(Settings().postgres_dsn) as conn:
        row = conn.execute(
            "SELECT pinned_at IS NOT NULL FROM dataset_items WHERE source_trace_id = %s", (kept,)
        ).fetchone()
    assert row == (True,)
