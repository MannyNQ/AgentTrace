"""Helpers for integration tests against the compose stack."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg

from agenttrace import Span, new_span_id, new_trace_id
from agenttrace.pricing import PriceTable
from agenttrace_ingest.auth import DEFAULT_PROJECT_ID, hash_token
from agenttrace_ingest.settings import Settings
from agenttrace_worker.pipeline import to_row
from agenttrace_worker.settings import Settings as WorkerSettings
from agenttrace_worker.stores import ClickHouse, Payloads


def emit_root(
    input_value: Any,
    age: timedelta = timedelta(seconds=1),
    retention_days=30,
    attributes: dict[str, str] | None = None,
    project_id: UUID = DEFAULT_PROJECT_ID,
) -> str:
    """Store a one-span trace the way the worker does, offloading a large input to S3. `age`
    backdates it, so a test can make it older than its retention."""
    ws = WorkerSettings()
    trace_id = new_trace_id()
    start = datetime.now(UTC) - age
    span = Span(
        trace_id=trace_id,
        span_id=new_span_id(),
        name="integration.root",
        kind="agent",
        start_time=start,
        end_time=start + timedelta(milliseconds=5),
        input=input_value,
        attributes=attributes or {},
    )
    row = to_row(span, project_id, retention_days, PriceTable(), Payloads(ws).put)
    ClickHouse(ws).insert_spans([row])
    return trace_id


def emit_spans(spans: list[Span], project_id: UUID = DEFAULT_PROJECT_ID) -> None:
    """Store spans the way the worker does."""
    ws = WorkerSettings()
    put = Payloads(ws).put
    ClickHouse(ws).insert_spans([to_row(s, project_id, 30, PriceTable(), put) for s in spans])


def register_key(
    s: Settings, key: str, project_id: UUID = DEFAULT_PROJECT_ID, scope: str = "full"
) -> None:
    """A live API key for a project, as an owner would create one."""
    with psycopg.connect(s.postgres_dsn) as conn:
        conn.execute(
            "INSERT INTO api_keys (key_hash, project_id, name, scope, hint)"
            " VALUES (%s, %s, %s, %s, %s)",
            (hash_token(key), project_id, "test", scope, key[-4:]),
        )
