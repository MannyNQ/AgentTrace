"""The span worker: reads ingest batches from the Redis stream and stores them in ClickHouse.

Delivery is at-least-once (ADR-0015). Each read first moves entries that failed too often to the
dead-letter stream, then reclaims entries a crashed worker left pending, then reads new ones.
While a store is down, the worker holds its current entries and retries them with backoff
instead of re-reading, so an outage doesn't count against an entry's deliveries.
"""

import json
import logging
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

import redis
from pydantic import TypeAdapter, ValidationError

from agenttrace import Span
from agenttrace_worker.pipeline import PayloadLimits, to_row
from agenttrace_worker.settings import Settings
from agenttrace_worker.stores import (
    TRANSIENT_ERRORS,
    ClickHouse,
    Dedup,
    Payloads,
    Postgres,
)

log = logging.getLogger("agenttrace.worker")

STREAM = "spans"  # written by agenttrace_ingest.spans
GROUP = "workers"
DEAD_STREAM = "spans:dead"
DEAD_MAXLEN = 100_000
HEARTBEAT_KEY = "worker:heartbeat:{consumer}"

Entry = tuple[str, dict[str, str]]
SPANS = TypeAdapter(list[Span])


@dataclass
class Batch:
    entry_id: str
    project_id: UUID
    spans: list[Span]


def parse(entry: Entry) -> Batch:
    entry_id, fields = entry
    return Batch(entry_id, UUID(fields["project_id"]), SPANS.validate_json(fields["spans"]))


class Store(Protocol):
    def __call__(self, batches: list[Batch]) -> None: ...


class SpanStore:
    """Dedup, offload payloads, price, and insert one group of batches in one ClickHouse insert."""

    def __init__(self, s: Settings, r: redis.Redis):
        self.postgres = Postgres(s)
        self.clickhouse = ClickHouse(s)
        self.payloads = Payloads(s)
        self.dedup = Dedup(r, s.dedup_ttl_s)
        self.limits = PayloadLimits(s.payload_offload_bytes, s.payload_preview_bytes)

    def __call__(self, batches: list[Batch]) -> None:
        unseen = self.dedup.unseen(sp for b in batches for sp in b.spans)
        prices = self.postgres.prices.get()
        rows, stored = [], []
        for b in batches:
            days = self.postgres.retention_days(b.project_id)
            for sp in b.spans:
                key = Dedup.key(sp)
                if key not in unseen:
                    continue
                unseen.discard(key)  # also drops a repeat within this read
                rows.append(to_row(sp, b.project_id, days, prices, self.payloads.put, self.limits))
                stored.append(sp)
        if rows:
            self.clickhouse.insert_spans(rows)
            self.dedup.mark(stored)  # only after the insert succeeded (ADR-0014)
        skipped = sum(len(b.spans) for b in batches) - len(rows)
        log.info("stored %d spans (%d duplicates skipped)", len(rows), skipped)


def ensure_group(r: redis.Redis) -> None:
    """Create the stream and consumer group if missing. Safe to call on every startup."""
    try:
        r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


class Worker:
    def __init__(self, r: redis.Redis, s: Settings, store: Store):
        self.r, self.s, self.store = r, s, store
        self.held: list[Batch] = []  # batches waiting out a store outage

    def run_once(self) -> int:
        self.r.set(HEARTBEAT_KEY.format(consumer=self.s.consumer_name), "1", ex=30)
        if self.held:
            batches = self.held
        else:
            self.dead_letter_exhausted()
            entries = self.reclaim() or self.read()
            if not entries:
                return 0
            batches = self.parse_all(entries)
        try:
            done = self.handle(batches)
        except TRANSIENT_ERRORS:
            self.held = batches
            raise
        self.held = []
        if done:
            self.r.xack(STREAM, GROUP, *done)
        return len(batches)

    def read(self) -> list[Entry]:
        raw = self.r.xreadgroup(
            GROUP,
            self.s.consumer_name,
            {STREAM: ">"},
            count=self.s.batch_size,
            block=self.s.block_ms,
        )
        # redis-py's return type is a wide union; with decode_responses=True it is this shape.
        resp = cast(list[tuple[str, list[Entry]]], raw)
        return resp[0][1] if resp else []

    def reclaim(self) -> list[Entry]:
        """Entries another (likely crashed) consumer, or an earlier failure, left pending."""
        resp = cast(
            list,
            self.r.xautoclaim(
                STREAM,
                GROUP,
                self.s.consumer_name,
                min_idle_time=self.s.reclaim_idle_ms,
                start_id="0-0",
                count=self.s.batch_size,
            ),
        )
        entries = [(eid, fields) for eid, fields in resp[1] if fields]
        if entries:
            log.warning("reclaimed %d pending entries", len(entries))
        return entries

    def dead_letter_exhausted(self) -> None:
        pending = cast(
            list[dict],
            self.r.xpending_range(
                STREAM, GROUP, min="-", max="+", count=100, idle=self.s.reclaim_idle_ms
            ),
        )
        for p in pending:
            if p["times_delivered"] >= self.s.max_deliveries:
                found = cast(list[Entry], self.r.xrange(STREAM, p["message_id"], p["message_id"]))
                fields = found[0][1] if found else {}
                self.dead_letter(p["message_id"], fields, f"delivered {p['times_delivered']} times")

    def dead_letter(self, entry_id: str, fields: dict[str, str], reason: str) -> None:
        log.error("dead-lettering entry %s: %s", entry_id, reason)
        self.r.xadd(
            DEAD_STREAM,
            {**fields, "original_id": entry_id, "reason": reason},
            maxlen=DEAD_MAXLEN,
            approximate=True,
        )
        self.r.xack(STREAM, GROUP, entry_id)

    def parse_all(self, entries: list[Entry]) -> list[Batch]:
        batches = []
        for entry in entries:
            try:
                batches.append(parse(entry))
            except (KeyError, ValueError, ValidationError, json.JSONDecodeError) as e:
                # Ingest validated it, so this is a bug; retrying can't fix it.
                self.dead_letter(entry[0], entry[1], f"unparseable: {type(e).__name__}: {e}")
        return batches

    def handle(self, batches: list[Batch]) -> list[str]:
        """Store what it can; returns the entry IDs to acknowledge. Raises on a store outage."""
        if not batches:
            return []
        try:
            self.store(batches)
            return [b.entry_id for b in batches]
        except TRANSIENT_ERRORS:
            raise
        except Exception:
            log.exception("storing %d batches failed; retrying one at a time", len(batches))
        done = []
        for b in batches:
            try:
                self.store([b])
                done.append(b.entry_id)
            except TRANSIENT_ERRORS:
                raise
            except Exception:
                # Left pending: reclaimed later, dead-lettered after max_deliveries.
                log.exception("entry %s failed; leaving it pending", b.entry_id)
        return done


def run_forever(
    worker: Worker,
    should_stop: Callable[[], bool],
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Consume until told to stop, riding out store outages instead of exiting."""
    delay = 1.0
    group_ready = False
    while not should_stop():
        try:
            if not group_ready:
                ensure_group(worker.r)  # also recreates the group if Redis came back without it
                group_ready = True
            worker.run_once()
            delay = 1.0
        except TRANSIENT_ERRORS as e:
            log.warning("store unavailable (%s: %s); retrying in %.0fs", type(e).__name__, e, delay)
            if isinstance(e, redis.ConnectionError | redis.TimeoutError):
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
    run_forever(Worker(r, s, SpanStore(s, r)), lambda: stopping)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
