"""The dataset API against the compose stack's Postgres, ClickHouse and S3."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from fastapi.testclient import TestClient
from stack import emit_root, register_key

from trayce_ingest.app import create_app
from trayce_ingest.auth import DEFAULT_PROJECT_ID
from trayce_ingest.dataset_store import PostgresDatasets
from trayce_ingest.datasets import Contents, add_item, publish
from trayce_ingest.errors import Refused
from trayce_ingest.migrate import migrate, pending
from trayce_ingest.settings import Settings

pytestmark = pytest.mark.integration


def test_migrations_are_listed_in_order_and_apply_once():
    assert [name for _, name, _ in pending(set())][:1] == ["0001_datasets.sql"]
    s = Settings()
    migrate(s.postgres_dsn)
    assert migrate(s.postgres_dsn) == []
    with psycopg.connect(s.postgres_dsn) as conn:
        row = conn.execute("SELECT count(*) FROM schema_migrations WHERE version = 1").fetchone()
    assert row == (1,)


@pytest.fixture(scope="module")
def api():
    s = Settings()
    key = f"test-{uuid.uuid4()}"
    with TestClient(create_app(s)) as c:  # lifespan runs the migrations
        register_key(s, key)
        c.headers["Authorization"] = f"Bearer {key}"
        yield c


def test_from_trace_reads_an_offloaded_input_from_s3(api: TestClient):
    blob = {"blob": "x" * 20_000}  # over the 8 KB offload threshold (ADR-0009)
    trace_id = emit_root(blob)
    ds = api.post("/v1/datasets", json={"name": f"it-{uuid.uuid4().hex[:8]}"}).json()["id"]

    r = api.post(f"/v1/datasets/{ds}/items/from-trace", json={"trace_id": trace_id})
    assert r.status_code == 201, r.text
    assert r.json()["contents"]["input"] == blob
    dup = api.post(f"/v1/datasets/{ds}/items/from-trace", json={"trace_id": trace_id})
    assert dup.status_code == 409
    missing = api.post(f"/v1/datasets/{ds}/items/from-trace", json={"trace_id": "e" * 32})
    assert missing.status_code == 404


def test_revisions_versions_and_deletes_in_postgres(api: TestClient):
    ds = api.post("/v1/datasets", json={"name": f"it-{uuid.uuid4().hex[:8]}"}).json()["id"]
    items = f"/v1/datasets/{ds}/items"
    a = api.post(items, json={"input": "a", "expected_output": "A"}).json()
    b = api.post(items, json={"input": "b", "expected_output": "B"}).json()
    assert api.post(f"/v1/datasets/{ds}/publish").json() == {"version": 1}

    edited = api.patch(
        f"{items}/{a['item_id']}", json={"rev": 1, "contents": {"input": "a", "expected_output": 2}}
    )
    assert edited.json()["rev"] == 2
    stale = api.patch(f"{items}/{a['item_id']}", json={"rev": 1, "contents": {"input": "a"}})
    assert stale.status_code == 409
    assert api.delete(f"{items}/{b['item_id']}?rev=1").status_code == 204
    assert api.post(f"/v1/datasets/{ds}/publish").json() == {"version": 2}

    v1 = api.get(f"/v1/datasets/{ds}/versions/1").json()
    assert [i["contents"]["expected_output"] for i in v1["items"]] == ["A", "B"]
    v2 = api.get(f"/v1/datasets/{ds}/versions/2").json()
    assert [(i["rev"], i["change"]) for i in v2["items"]] == [(2, "changed")]
    assert [r["item_id"] for r in v2["removed"]] == [b["item_id"]]

    # A never-published item is deleted outright; a published one is only dropped from the draft.
    c = api.post(items, json={"input": "c"}).json()
    assert api.delete(f"{items}/{c['item_id']}?rev=1").status_code == 204
    with psycopg.connect(Settings().postgres_dsn) as conn:
        rows = conn.execute(
            "SELECT item_id::text, deleted_at IS NOT NULL FROM dataset_items "
            "WHERE dataset_id = %s ORDER BY created_at",
            (ds,),
        ).fetchall()
    assert rows == [(a["item_id"], False), (b["item_id"], True)]


def test_concurrent_publishes_get_distinct_versions(api: TestClient):
    store = PostgresDatasets(Settings())
    dataset_id = store.create_dataset(DEFAULT_PROJECT_ID, f"it-{uuid.uuid4().hex[:8]}", "")
    add_item(store, DEFAULT_PROJECT_ID, dataset_id, Contents(input="a", expected_output="A"))

    def attempt(_) -> str:
        try:
            return f"v{publish(store, DEFAULT_PROJECT_ID, dataset_id)}"
        except Refused as e:
            return e.error

    with ThreadPoolExecutor(4) as pool:
        results = sorted(pool.map(attempt, range(4)))
    assert results[-1] == "v1"
    assert all(r.startswith("nothing to publish") for r in results[:-1])
