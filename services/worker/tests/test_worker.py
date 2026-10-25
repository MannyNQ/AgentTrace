import os

import pytest
import redis

from agenttrace_worker.main import GROUP, STREAM, Settings, ensure_group, run_once

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
