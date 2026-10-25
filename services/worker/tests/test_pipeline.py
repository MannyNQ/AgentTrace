from datetime import UTC, datetime
from uuid import UUID

import pytest

from agenttrace import Span, SpanKind, TraceMetadata, new_span_id, new_trace_id
from agenttrace_worker.pipeline import (
    COLUMNS,
    PayloadLimits,
    Price,
    PriceTable,
    store_payload,
    to_row,
    truncate_utf8,
)

PROJECT = UUID("00000000-0000-0000-0000-000000000001")
OCT = datetime(2026, 10, 1, tzinfo=UTC)
PRICES = PriceTable(
    {
        "m": [
            Price(datetime(2026, 1, 1, tzinfo=UTC), 4.0, 20.0),
            Price(datetime(2026, 9, 1, tzinfo=UTC), 2.0, 10.0),
        ]
    }
)


def llm(**over) -> Span:
    fields = dict(
        trace_id=new_trace_id(),
        span_id=new_span_id(),
        name="call",
        kind=SpanKind.LLM,
        start_time=OCT,
        model="m",
        input_tokens=1_000_000,
        output_tokens=100_000,
    )
    return Span(**(fields | over))


def test_cost_uses_the_price_in_effect_at_span_start():
    assert PRICES.cost(llm()) == 2.0 + 1.0
    assert PRICES.cost(llm(start_time=datetime(2026, 3, 1, tzinfo=UTC))) == 4.0 + 2.0


def test_cost_is_null_not_zero_when_it_cant_be_priced():
    assert PRICES.cost(llm(model="unknown")) is None
    assert PRICES.cost(llm(output_tokens=None)) is None
    assert PRICES.cost(llm(start_time=datetime(2025, 1, 1, tzinfo=UTC))) is None


def test_naive_start_time_is_read_as_utc():
    assert PRICES.cost(llm(start_time=datetime(2026, 10, 1))) == 3.0


def test_small_payload_stays_inline_whole():
    put = []
    preview, uri = store_payload({"a": 1}, "k", PayloadLimits(), lambda k, d: put.append(k) or "")
    assert (preview, uri, put) == ('{"a": 1}', "", [])


def test_large_payload_is_offloaded_with_a_preview():
    stored: dict[str, bytes] = {}

    def put(key: str, data: bytes) -> str:
        stored[key] = data
        return f"s3://b/{key}"

    value = "é" * 5000  # 10,000 bytes
    preview, uri = store_payload(value, "k", PayloadLimits(8192, 1001), put)
    assert uri == "s3://b/k" and stored["k"] == value.encode()
    assert preview == "é" * 500  # cut at 1001 bytes, the split character dropped


def test_truncate_never_splits_a_character():
    assert truncate_utf8("aé".encode(), 2) == "a"


def test_row_matches_columns_and_carries_trace_metadata():
    root = Span(
        trace_id=new_trace_id(),
        span_id=new_span_id(),
        name="run",
        kind=SpanKind.AGENT,
        start_time=OCT,
        input="q",
        trace=TraceMetadata(user_id="u", tags=["t"]),
    )
    row = dict(zip(COLUMNS, to_row(root, PROJECT, 7, PRICES, lambda k, d: ""), strict=True))
    assert row["parent_span_id"] == "" and row["user_id"] == "u" and row["tags"] == ["t"]
    assert row["input_preview"] == "q" and row["output_preview"] == ""
    assert row["cost_usd"] is None  # only LLM spans are priced
    assert row["retention_days"] == 7


def test_llm_row_is_priced():
    row = dict(zip(COLUMNS, to_row(llm(), PROJECT, 30, PRICES, lambda k, d: ""), strict=True))
    assert row["cost_usd"] == 3.0


CACHED = PriceTable(
    {
        "m": [Price(datetime(2026, 1, 1, tzinfo=UTC), 4.0, 20.0, 0.20, 5.0, 8.0)],
        "old": [PRICES.by_model["m"][0]],
    }
)


def test_cache_reads_and_writes_are_priced_on_top_of_input():
    span = llm(
        input_tokens=1_000,
        output_tokens=1_000,
        cache_read_tokens=1_000_000,
        cache_write_5m_tokens=1_000_000,
        cache_write_1h_tokens=1_000_000,
    )
    # 0.004 input + 0.02 output + 0.20 read + 5.00 write (5m) + 8.00 write (1h)
    assert CACHED.cost(span) == pytest.approx(13.224)


def test_cache_tokens_without_a_cache_price_make_cost_null_not_low():
    assert CACHED.cost(llm(model="old", cache_read_tokens=10)) is None
    assert CACHED.cost(llm(model="old", cache_read_tokens=0)) == 6.0  # unused cache is fine


def test_cache_columns_are_written():
    span = llm(cache_read_tokens=7, cache_write_5m_tokens=8, cache_write_1h_tokens=9)
    row = dict(zip(COLUMNS, to_row(span, PROJECT, 30, CACHED, lambda k, d: ""), strict=True))
    assert (
        row["cache_read_tokens"],
        row["cache_write_5m_tokens"],
        row["cache_write_1h_tokens"],
    ) == (7, 8, 9)
