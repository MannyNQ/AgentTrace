"""Trace reads against the compose stack: the summary query and its cursor, spans, a payload
offloaded to S3, outcomes, and live traces (ADR-0031)."""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from stack import emit_root, register_key

from agenttrace_ingest.app import create_app
from agenttrace_ingest.settings import Settings

pytestmark = pytest.mark.integration

BIG = {"question": "x" * 20_000}  # over the 8 KB offload threshold (ADR-0009)


@pytest.fixture(scope="module")
def api():
    s = Settings()
    key = f"test-{uuid.uuid4()}"
    with TestClient(create_app(s)) as c:
        register_key(s, key)
        c.headers["Authorization"] = f"Bearer {key}"
        yield c


def test_a_stored_trace_reads_back_with_its_offloaded_input(api: TestClient):
    trace_id = emit_root(BIG)
    detail = api.get(f"/v1/traces/{trace_id}").json()
    assert detail["summary"]["trace_id"] == trace_id and detail["summary"]["span_count"] == 1
    (root,) = detail["spans"]
    assert root["input_offloaded"] is True and len(root["input_preview"]) < 20_000
    full = api.get(f"/v1/traces/{trace_id}/spans/{root['span_id']}/input").json()["text"]
    assert '"question"' in full and len(full) > 20_000


def test_the_list_pages_past_a_cursor(api: TestClient):
    older, newer = emit_root({"q": 1}), emit_root({"q": 2})
    page = api.get("/v1/traces", params={"limit": 200}).json()
    ids = [t["trace_id"] for t in page["traces"]]
    assert ids.index(newer) < ids.index(older)
    first = api.get("/v1/traces", params={"limit": 1}).json()
    nxt = first["next"]
    rest = api.get(
        "/v1/traces",
        params={"limit": 200, "before_start": nxt["start_time"], "before_trace": nxt["trace_id"]},
    ).json()
    assert first["traces"][0]["trace_id"] not in [t["trace_id"] for t in rest["traces"]]


def test_outcome_and_live(api: TestClient):
    trace_id = emit_root({"q": "where is order 1042?"})
    assert api.get(f"/v1/traces/{trace_id}/outcome").json()["trajectory"] == []
    missing = "e" * 32
    live = api.post("/v1/traces/live", json={"trace_ids": [missing, trace_id]}).json()
    assert live == {"trace_ids": [trace_id]}


def test_another_project_s_key_sees_nothing(api: TestClient):
    trace_id = emit_root({"q": 3})
    s, other_project, key = Settings(), uuid.uuid4(), f"test-{uuid.uuid4()}"
    with psycopg.connect(s.postgres_dsn) as conn:
        conn.execute(
            "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, %s, %s)",
            (other_project, "00000000-0000-0000-0000-000000000001", f"it-{other_project}",
             f"it-{other_project.hex[:12]}"),
        )  # fmt: skip
    register_key(s, key, other_project)
    r = api.get(f"/v1/traces/{trace_id}", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 404
