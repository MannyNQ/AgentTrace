"""The worker's adapters to Postgres, ClickHouse, S3 and the Redis dedup marks."""

import time
from collections.abc import Callable, Iterable
from typing import Any, LiteralString
from uuid import UUID

import boto3
import botocore.exceptions
import clickhouse_connect
import clickhouse_connect.driver.exceptions
import psycopg
import redis

from agenttrace import Span
from agenttrace_worker.pipeline import COLUMNS, Price, PriceTable
from agenttrace_worker.settings import Settings

# Store failures worth waiting out (the store is down or unreachable), as opposed to a batch the
# store rejects, which would fail the same way on every retry.
TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    redis.ConnectionError,
    redis.TimeoutError,
    clickhouse_connect.driver.exceptions.OperationalError,
    botocore.exceptions.EndpointConnectionError,
    botocore.exceptions.ConnectionClosedError,
    botocore.exceptions.ReadTimeoutError,
    psycopg.OperationalError,
    ConnectionError,
    TimeoutError,
)

DEDUP_KEY = "seen:{trace_id}:{span_id}"


class Cached[T]:
    """A value loaded on first use and reloaded once it's older than `ttl_s`."""

    def __init__(self, load: Callable[[], T], ttl_s: float, clock=time.monotonic):
        self._load, self._ttl, self._clock = load, ttl_s, clock
        self._value: T | None = None
        self._at = 0.0

    def get(self) -> T:
        if self._value is None or self._clock() - self._at > self._ttl:
            self._value = self._load()
            self._at = self._clock()
        return self._value


class Postgres:
    def __init__(self, s: Settings):
        self.s = s
        self.prices = Cached(self._load_prices, s.cache_ttl_s)
        self.retention = Cached(self._load_retention, s.cache_ttl_s)

    def _query(self, sql: LiteralString) -> list[tuple]:
        with psycopg.connect(self.s.postgres_dsn, connect_timeout=5) as conn:
            return conn.execute(sql).fetchall()

    def _load_prices(self) -> PriceTable:
        table = PriceTable()
        for model, eff, *rates in self._query(
            "SELECT model, effective_from, input_per_mtok, output_per_mtok, cache_read_per_mtok, "
            "cache_write_5m_per_mtok, cache_write_1h_per_mtok "
            "FROM model_prices ORDER BY model, effective_from"
        ):
            inp, out, read, w5m, w1h = (None if r is None else float(r) for r in rates)
            assert inp is not None and out is not None  # NOT NULL columns
            table.by_model.setdefault(model, []).append(Price(eff, inp, out, read, w5m, w1h))
        return table

    def _load_retention(self) -> dict[UUID, int]:
        return {pid: days for pid, days in self._query("SELECT id, retention_days FROM projects")}

    def retention_days(self, project_id: UUID) -> int:
        return self.retention.get().get(project_id, self.s.default_retention_days)


class ClickHouse:
    def __init__(self, s: Settings):
        self.s = s
        self._client = None

    def insert_spans(self, rows: list[list[Any]]) -> None:
        if self._client is None:
            self._client = clickhouse_connect.get_client(
                host=self.s.clickhouse_host,
                port=self.s.clickhouse_port,
                username=self.s.clickhouse_user,
                password=self.s.clickhouse_password,
                database=self.s.clickhouse_database,
            )
        self._client.insert("spans", rows, column_names=COLUMNS)


class Payloads:
    def __init__(self, s: Settings):
        self.bucket = s.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name="us-east-1",
        )

    def put(self, key: str, data: bytes) -> str:
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType="application/json"
        )
        return f"s3://{self.bucket}/{key}"


class Dedup:
    """ADR-0014: spans already stored in the last `ttl_s` seconds are skipped."""

    def __init__(self, r: redis.Redis, ttl_s: int):
        self.r, self.ttl_s = r, ttl_s

    @staticmethod
    def key(span: Span) -> str:
        return DEDUP_KEY.format(trace_id=span.trace_id, span_id=span.span_id)

    def unseen(self, spans: Iterable[Span]) -> set[str]:
        keys = list({self.key(s) for s in spans})
        if not keys:
            return set()
        pipe = self.r.pipeline(transaction=False)
        for k in keys:
            pipe.exists(k)
        return {k for k, seen in zip(keys, pipe.execute(), strict=True) if not seen}

    def mark(self, spans: Iterable[Span]) -> None:
        pipe = self.r.pipeline(transaction=False)
        for s in spans:
            pipe.set(self.key(s), "1", ex=self.ttl_s)
        pipe.execute()
