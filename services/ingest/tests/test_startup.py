"""Ingest's startup: ClickHouse migrations retry beside the app, so ingest serves while ClickHouse
is down (ADR-0015)."""

import logging
import threading
import time

from fake_auth import FakeAuth, FakeLimiter
from fake_datasets import FakeDatasets, FakePins
from fastapi.testclient import TestClient

from trayce_ingest import app as app_module
from trayce_ingest.auth import DEFAULT_PROJECT_ID, DEV_KEY
from trayce_ingest.settings import Settings

FIRST = 0.05  # the first retry delay, shortened for the test


def test_ingest_serves_while_clickhouse_migrations_retry(monkeypatch):
    attempts: list[float] = []
    applied = threading.Event()

    def migrate_clickhouse(dsn, client):
        attempts.append(time.monotonic())
        if len(attempts) < 3:
            raise ConnectionError("clickhouse is down")
        applied.set()
        return ["0001_replay_of.sql"]

    monkeypatch.setattr(app_module, "migrate", lambda dsn: [])
    monkeypatch.setattr(app_module, "migrate_clickhouse", migrate_clickhouse)
    monkeypatch.setattr(app_module, "clickhouse_client", lambda s: object())
    monkeypatch.setattr(app_module, "ensure_bucket", lambda s: None)
    monkeypatch.setattr(app_module, "CLICKHOUSE_RETRY_FIRST_S", FIRST)
    app = app_module.create_app(
        Settings(trayce_api_key=None),
        checks={},
        datasets=FakeDatasets(),
        pins=FakePins(),
        auth_store=FakeAuth(),
        limiter=FakeLimiter(),
    )
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200  # up before ClickHouse answered
        assert applied.wait(timeout=5)
    assert len(attempts) == 3
    # Backs off: the waits are FIRST, then twice that. Each gap is at least its wait (startup
    # work on a busy machine only adds to it), so compare with the waits, not with each other.
    assert attempts[1] - attempts[0] >= FIRST
    assert attempts[2] - attempts[1] >= 2 * FIRST


def test_shutdown_stops_a_migration_still_retrying(monkeypatch):
    def down(dsn, client):
        raise ConnectionError("clickhouse is down")

    monkeypatch.setattr(app_module, "migrate", lambda dsn: [])
    monkeypatch.setattr(app_module, "migrate_clickhouse", down)
    monkeypatch.setattr(app_module, "clickhouse_client", lambda s: object())
    monkeypatch.setattr(app_module, "ensure_bucket", lambda s: None)
    app = app_module.create_app(
        Settings(trayce_api_key=None),
        checks={},
        datasets=FakeDatasets(),
        pins=FakePins(),
        auth_store=FakeAuth(),
        limiter=FakeLimiter(),
    )
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
    # Leaving the block ran shutdown, which cancels the retry loop instead of hanging on it.


def _start(monkeypatch, **settings) -> TestClient:
    monkeypatch.setattr(app_module, "migrate", lambda dsn: [])
    monkeypatch.setattr(app_module, "migrate_clickhouse", lambda dsn, client: [])
    monkeypatch.setattr(app_module, "clickhouse_client", lambda s: object())
    monkeypatch.setattr(app_module, "ensure_bucket", lambda s: None)
    store = FakeAuth()
    store.add_project(DEFAULT_PROJECT_ID, "default")
    app = app_module.create_app(
        Settings(trayce_api_key=DEV_KEY, **settings),
        checks={},
        datasets=FakeDatasets(),
        pins=FakePins(),
        auth_store=store,
        limiter=FakeLimiter(),
    )
    return TestClient(app)


def test_the_dev_key_works_while_the_public_url_is_local(monkeypatch, caplog):
    with _start(monkeypatch) as c:  # TRAYCE_PUBLIC_URL unset: localhost
        r = c.get("/v1/datasets", headers={"Authorization": f"Bearer {DEV_KEY}"})
        assert r.status_code == 200
    assert "anyone who can reach ingest" in caplog.text  # it says the key is live


def test_the_dev_key_is_refused_on_a_public_install(monkeypatch, caplog):
    with _start(monkeypatch, trayce_public_url="https://trayce.example.com") as c:
        r = c.get("/v1/datasets", headers={"Authorization": f"Bearer {DEV_KEY}"})
        assert r.status_code == 401
    assert "published dev key" in caplog.text


def test_link_tokens_stay_out_of_the_access_log(caplog):
    app_module.create_app(Settings(trayce_api_key=None), checks={}, auth_store=FakeAuth())
    access = logging.getLogger("uvicorn.access")
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        access.info(
            '%s - "%s %s HTTP/%s" %d',
            "10.0.0.1:5000",
            "POST",
            "/v1/auth/links/s3cr3t-token/reset",
            "1.1",
            204,
        )
    assert "s3cr3t-token" not in caplog.text
    assert "/v1/auth/links/" in caplog.text
