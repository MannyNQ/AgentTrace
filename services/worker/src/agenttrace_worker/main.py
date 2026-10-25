"""Phase 0 worker: joins the span stream's consumer group and acknowledges entries.

Phase 1 replaces handle_batch with trace assembly, cost, payload offload, and ClickHouse writes.
"""

import logging
import signal
import socket
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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    s = Settings()
    r = redis.Redis.from_url(s.redis_url, decode_responses=True)
    ensure_group(r)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("worker %s consuming %s/%s", s.consumer_name, STREAM, GROUP)
    while not stopping:
        run_once(r, s)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
