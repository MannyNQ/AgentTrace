"""Helpers for integration tests against the compose stack."""

from datetime import UTC, datetime, timedelta
from typing import Any

from agenttrace import Span, new_span_id, new_trace_id
from agenttrace.pricing import PriceTable
from agenttrace_ingest.spans import DEFAULT_PROJECT_ID
from agenttrace_worker.pipeline import to_row
from agenttrace_worker.settings import Settings as WorkerSettings
from agenttrace_worker.stores import ClickHouse, Payloads


def emit_root(
    input_value: Any,
    age: timedelta = timedelta(seconds=1),
    retention_days=30,
    attributes: dict[str, str] | None = None,
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
    row = to_row(span, DEFAULT_PROJECT_ID, retention_days, PriceTable(), Payloads(ws).put)
    ClickHouse(ws).insert_spans([row])
    return trace_id
