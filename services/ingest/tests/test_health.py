import pytest
from fastapi.testclient import TestClient

from agenttrace_ingest.app import create_app
from agenttrace_ingest.settings import Settings


def ok(_s):
    pass


def broken(_s):
    raise ConnectionError("refused")


def client(checks) -> TestClient:
    # Not entered as a context manager, so lifespan (bucket setup) doesn't run.
    return TestClient(create_app(Settings(health_timeout_s=0.5), checks=checks))


def test_all_ok():
    r = client({"a": ok, "b": ok}).get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "stores": {"a": "ok", "b": "ok"}}


def test_one_store_down_is_503_and_named():
    r = client({"a": ok, "b": broken}).get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["stores"]["a"] == "ok"
    assert body["stores"]["b"] == "error: ConnectionError"  # the message is logged, not served


@pytest.mark.integration
def test_live_stack_is_healthy():
    with TestClient(create_app()) as c:
        r = c.get("/health")
    assert r.status_code == 200, r.json()
