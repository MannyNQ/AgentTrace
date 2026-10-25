import os

import pytest
import redis

from agenttrace_worker.main import GROUP, STREAM, Settings, ensure_group, run_forever, run_once

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/15")


@pytest.mark.integration
def test_consumes_and_acks_entries():
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    r.delete(STREAM)
    ensure_group(r)
    ensure_group(r)  # idempotent
    r.xadd(STREAM, {"span": "{}"})
    r.xadd(STREAM, {"span": "{}"})

    assert run_once(r, Settings(redis_url=REDIS_URL, consumer_name="t", block_ms=100)) == 2
    assert r.xpending(STREAM, GROUP)["pending"] == 0
    r.delete(STREAM)


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

    def xreadgroup(self, *a, **kw):
        self._maybe_fail()
        self.reads += 1
        return []


def test_survives_redis_outage_with_backoff():
    r = FlakyRedis(outages=3)
    sleeps: list[float] = []
    run_forever(
        r,  # type: ignore[arg-type]
        Settings(consumer_name="t"),
        should_stop=lambda: r.reads >= 2,
        sleep=sleeps.append,
    )
    assert sleeps == [1.0, 2.0, 4.0]  # exponential backoff, no crash
    assert r.groups_created == 1  # group re-ensured once Redis is back
    assert r.reads == 2  # kept consuming afterwards
