"""Turn validated spans into ClickHouse rows: payload previews and offload (ADR-0009), and cost
(ADR-0010, via `trayce.pricing`). Pure apart from the `put_payload` callback, so it's
testable without the stores."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from trayce import Span
from trayce.pricing import PriceTable, as_utc

COLUMNS = [
    "project_id", "trace_id", "span_id", "parent_span_id", "name", "kind", "start_time",
    "end_time", "status", "error_type", "model", "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens", "cost_usd",
    "input_preview", "output_preview", "input_uri", "output_uri", "session_id", "user_id",
    "tags", "metadata", "attributes", "retention_days",
]  # fmt: skip

PutPayload = Callable[[str, bytes], str]  # (object key, bytes) -> uri


def truncate_utf8(data: bytes, limit: int) -> str:
    """The first `limit` bytes as text, dropping a character split at the cut."""
    return data[:limit].decode("utf-8", errors="ignore")


@dataclass(frozen=True)
class PayloadLimits:
    offload_bytes: int = 8 * 1024
    preview_bytes: int = 1024


DEFAULT_LIMITS = PayloadLimits()


def store_payload(value: Any, key: str, limits: PayloadLimits, put: PutPayload) -> tuple[str, str]:
    """(preview, uri). Small values stay inline whole; large ones go to object storage."""
    if value is None:
        return "", ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    data = text.encode()
    if len(data) <= limits.offload_bytes:
        return text, ""
    return truncate_utf8(data, limits.preview_bytes), put(key, data)


def to_row(
    span: Span,
    project_id: UUID,
    retention_days: int,
    prices: PriceTable,
    put: PutPayload,
    limits: PayloadLimits = DEFAULT_LIMITS,
) -> list[Any]:
    key = f"{project_id}/{span.trace_id}/{span.span_id}"
    input_preview, input_uri = store_payload(span.input, f"{key}/input.json", limits, put)
    output_preview, output_uri = store_payload(span.output, f"{key}/output.json", limits, put)
    meta = span.trace
    return [
        project_id,
        span.trace_id,
        span.span_id,
        span.parent_span_id or "",
        span.name,
        span.kind.value,
        as_utc(span.start_time),
        as_utc(span.end_time) if span.end_time else None,
        span.status.value,
        span.error_type or "",
        span.model or "",
        span.input_tokens,
        span.output_tokens,
        span.cache_read_tokens,
        span.cache_write_5m_tokens,
        span.cache_write_1h_tokens,
        prices.cost(span, span.start_time) if span.kind == "llm" else None,
        input_preview,
        output_preview,
        input_uri,
        output_uri,
        (meta.session_id or "") if meta else "",
        (meta.user_id or "") if meta else "",
        meta.tags if meta else [],
        meta.metadata if meta else {},
        span.attributes,
        retention_days,
    ]
