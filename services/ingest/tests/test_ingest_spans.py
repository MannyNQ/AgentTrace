import json
from uuid import UUID

import redis
from fake_auth import FakeAuth
from fastapi.testclient import TestClient

from agenttrace import new_span_id, new_trace_id
from agenttrace_ingest.app import create_app
from agenttrace_ingest.settings import Settings
from agenttrace_ingest.spans import RedisQueue

PROJECT = UUID("00000000-0000-0000-0000-0000000000aa")


KEYS = {"good": PROJECT}


class Queue:
    def __init__(self):
        self.pushed: list[tuple[UUID, list]] = []

    async def push(self, project_id: UUID, spans_json: str) -> None:
        self.pushed.append((project_id, json.loads(spans_json)))


def span(**over) -> dict:
    return {
        "trace_id": new_trace_id(),
        "span_id": new_span_id(),
        "name": "run",
        "kind": "agent",
        "start_time": "2026-10-25T12:00:00Z",
        **over,
    }


def setup(**settings):
    q = Queue()
    app = create_app(Settings(**settings), checks={}, auth_store=FakeAuth.with_keys(KEYS), queue=q)
    return TestClient(app), q


def post(c: TestClient, body, key="good"):
    return c.post("/v1/spans", json=body, headers={"Authorization": f"Bearer {key}"})


def test_valid_batch_is_queued_under_the_keys_project():
    c, q = setup()
    r = post(c, {"spans": [span(), span(name="tool", kind="tool")]})
    assert r.status_code == 202 and r.json() == {"accepted": 2}
    project_id, queued = q.pushed[0]
    assert project_id == PROJECT
    assert [s["name"] for s in queued] == ["run", "tool"]


def test_missing_or_unknown_key_is_401():
    c, q = setup()
    assert c.post("/v1/spans", json={"spans": [span()]}).status_code == 401
    assert post(c, {"spans": [span()]}, key="nope").status_code == 401
    assert q.pushed == []


def test_one_invalid_span_rejects_the_whole_batch():
    c, q = setup()
    r = post(c, {"spans": [span(), span(kind="generation")]})
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["spans", 1, "kind"]
    assert q.pushed == []


def test_too_many_spans_is_422():
    c, q = setup(max_spans_per_request=2)
    assert post(c, {"spans": [span(), span(), span()]}).status_code == 422
    assert q.pushed == []


def test_oversized_body_is_413():
    c, q = setup(max_request_bytes=500)
    r = post(c, {"spans": [span(name="x" * 600)]})
    assert r.status_code == 413
    assert q.pushed == []


def test_empty_batch_is_accepted_and_not_queued():
    c, q = setup()
    assert post(c, {"spans": []}).json() == {"accepted": 0}
    assert q.pushed == []


class FullRedis:
    async def xadd(self, *args, **kwargs):
        raise redis.ResponseError("OOM command not allowed when used memory > 'maxmemory'.")


def test_a_full_redis_is_503_not_500():
    q = RedisQueue(Settings())
    q.r = FullRedis()  # type: ignore[assignment]
    app = create_app(Settings(), checks={}, auth_store=FakeAuth.with_keys(KEYS), queue=q)
    r = post(TestClient(app), {"spans": [span()]})
    assert r.status_code == 503
    assert "full" in r.json()["detail"]
