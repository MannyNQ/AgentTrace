"""Phase 0 worker: joins the span stream's consumer group and acknowledges entries.

Phase 1 replaces handle_batch with trace assembly, cost, payload offload, and ClickHouse writes.
"""

import logging
import signal
import socket
import time
from collections.abc import Callable
from typing import cast

import redis
from pydantic_settings import BaseSettings

log = logging.getLogger("agenttrace.worker")

STREAM = "spans"
GROUP = "workers"
HEARTBEAT_KEY = "worker:heartbeat:{consumer}"


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"
    consumer_name: str = socket.gethostname()
    batch_size: int = 500
    block_ms: int = 1000


def ensure_group(r: redis.Redis) -> None:
    """Create the stream and consumer group if missing. Safe to call on every startup."""
    try:
        r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


Entry = tuple[str, dict[str, str]]


def handle_batch(entries: list[Entry]) -> None:
    log.info("received %d span entries (Phase 0: acknowledged, not stored)", len(entries))


def run_once(r: redis.Redis, s: Settings) -> int:
    r.set(HEARTBEAT_KEY.format(consumer=s.consumer_name), "1", ex=30)
    raw = r.xreadgroup(GROUP, s.consumer_name, {STREAM: ">"}, count=s.batch_size, block=s.block_ms)
    # redis-py's return type is a wide union; with decode_responses=True it is this shape.
    resp = cast(list[tuple[str, list[Entry]]], raw)
    if not resp:
        return 0
    entries = resp[0][1]
    handle_batch(entries)
    r.xack(STREAM, GROUP, *(entry_id for entry_id, _ in entries))
    return len(entries)


def run_forever(
    r: redis.Redis,
    s: Settings,
    should_stop: Callable[[], bool],
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Consume until told to stop, riding out Redis restarts instead of exiting."""
    delay = 1.0
    group_ready = False
    while not should_stop():
        try:
            if not group_ready:
                ensure_group(r)  # also recreates the group if Redis came back without it
                group_ready = True
            run_once(r, s)
            delay = 1.0
        except (redis.ConnectionError, redis.TimeoutError) as e:
            log.warning("redis unavailable (%s); retrying in %.0fs", e, delay)
            group_ready = False
            sleep(delay)
            delay = min(delay * 2, 30.0)
        except redis.ResponseError as e:
            if "NOGROUP" not in str(e):
                raise
            group_ready = False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    s = Settings()
    r = redis.Redis.from_url(s.redis_url, decode_responses=True)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("worker %s consuming %s/%s", s.consumer_name, STREAM, GROUP)
    run_forever(r, s, lambda: stopping)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
