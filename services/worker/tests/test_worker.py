import json
import os
from datetime import UTC, datetime
from uuid import UUID

import pytest
import redis

from agenttrace import new_span_id, new_trace_id
from agenttrace_worker.main import (
    DEAD_STREAM,
    GROUP,
    STREAM,
    Batch,
    Worker,
    ensure_group,
    run_forever,
)
from agenttrace_worker.settings import Settings

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/15")
PROJECT = "00000000-0000-0000-0000-000000000001"


def span_json(name="s") -> dict:
    return {
        "trace_id": new_trace_id(),
        "span_id": new_span_id(),
        "name": name,
        "kind": "custom",
        "start_time": datetime.now(UTC).isoformat(),
    }


def entry_fields(*names: str) -> dict[str, str]:
    return {"project_id": PROJECT, "spans": json.dumps([span_json(n) for n in names])}


class RecordingStore:
    """Stores everything except batches containing a span named `poison`."""

    def __init__(self, outages: int = 0):
        self.stored: list[str] = []
        self.outages = outages

    def __call__(self, batches: list[Batch]) -> None:
        if self.outages:
            self.outages -= 1
            raise ConnectionError("clickhouse down")
        if any(sp.name == "poison" for b in batches for sp in b.spans):
            raise ValueError("ClickHouse rejected the row")
        self.stored += [sp.name for b in batches for sp in b.spans]


# --- against a real Redis (docker compose stack) ---


@pytest.fixture
def r():
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    client.delete(STREAM, DEAD_STREAM)
    ensure_group(client)
    ensure_group(client)  # idempotent
    yield client
    client.delete(STREAM, DEAD_STREAM)


def settings(**over) -> Settings:
    return Settings(redis_url=REDIS_URL, consumer_name="t", block_ms=100, **over)


@pytest.mark.integration
def test_stores_and_acks_entries(r):
    r.xadd(STREAM, entry_fields("a", "b"))
    r.xadd(STREAM, entry_fields("c"))
    store = RecordingStore()
    assert Worker(r, settings(), store).run_once() == 2
    assert store.stored == ["a", "b", "c"]
    assert r.xpending(STREAM, GROUP)["pending"] == 0


@pytest.mark.integration
def test_poison_batch_is_isolated_left_pending_then_dead_lettered(r):
    r.xadd(STREAM, entry_fields("ok"))
    poison_id = r.xadd(STREAM, entry_fields("poison"))
    store = RecordingStore()
    w = Worker(r, settings(reclaim_idle_ms=0, max_deliveries=3), store)

    w.run_once()
    assert store.stored == ["ok"]  # the good batch isn't held back by the bad one
    assert r.xpending(STREAM, GROUP)["pending"] == 1

    w.run_once()  # reclaimed: delivery 2
    w.run_once()  # reclaimed: delivery 3
    w.run_once()  # exhausted: dead-lettered
    assert r.xpending(STREAM, GROUP)["pending"] == 0
    dead = r.xrange(DEAD_STREAM)
    assert dead[0][1]["original_id"] == poison_id
    assert dead[0][1]["reason"] == "delivered 3 times"


@pytest.mark.integration
def test_unparseable_entry_is_dead_lettered_at_once(r):
    r.xadd(STREAM, {"project_id": PROJECT, "spans": "not json"})
    Worker(r, settings(), RecordingStore()).run_once()
    assert r.xpending(STREAM, GROUP)["pending"] == 0
    assert r.xrange(DEAD_STREAM)[0][1]["reason"].startswith("unparseable")


@pytest.mark.integration
def test_store_outage_holds_batches_without_new_deliveries(r):
    r.xadd(STREAM, entry_fields("a"))
    store = RecordingStore(outages=2)
    w = Worker(r, settings(reclaim_idle_ms=0), store)
    for _ in range(2):
        with pytest.raises(ConnectionError):
            w.run_once()
    assert r.xpending_range(STREAM, GROUP, "-", "+", 10)[0]["times_delivered"] == 1
    w.run_once()
    assert store.stored == ["a"]
    assert r.xpending(STREAM, GROUP)["pending"] == 0


# --- without Redis ---


class FlakyRedis:
    """Fails the first `outages` calls with ConnectionError, then behaves like an empty stream."""

    def __init__(self, outages: int):
        self.outages = outages
        self.groups_created = 0
        self.reads = 0

    def _maybe_fail(self):
        if self.outages:
            self.outages -= 1
            raise redis.ConnectionError("Connection closed by server.")

    def xgroup_create(self, *a, **kw):
        self._maybe_fail()
        self.groups_created += 1

    def set(self, *a, **kw):
        self._maybe_fail()

    def xpending_range(self, *a, **kw):
        return []

    def xautoclaim(self, *a, **kw):
        return ["0-0", [], []]

    def xreadgroup(self, *a, **kw):
        self._maybe_fail()
        self.reads += 1
        return []


def test_survives_redis_outage_with_backoff():
    r = FlakyRedis(outages=3)
    sleeps: list[float] = []
    worker = Worker(r, Settings(consumer_name="t"), RecordingStore())  # type: ignore[arg-type]
    run_forever(worker, should_stop=lambda: r.reads >= 2, sleep=sleeps.append)
    assert sleeps == [1.0, 2.0, 4.0]  # exponential backoff, no crash
    assert r.groups_created == 1  # group re-ensured once Redis is back
    assert r.reads == 2  # kept consuming afterwards


def test_batch_parse_keeps_project():
    from agenttrace_worker.main import parse

    b = parse(("1-0", entry_fields("x")))
    assert b.project_id == UUID(PROJECT) and b.spans[0].name == "x"
