from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from trayce import Span, SpanKind, TraceMetadata, new_span_id, new_trace_id

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def root(**overrides) -> Span:
    fields = {
        "trace_id": new_trace_id(),
        "span_id": new_span_id(),
        "name": "run",
        "kind": SpanKind.AGENT,
        "start_time": T0,
    }
    return Span(**(fields | overrides))


def test_ids_are_otel_format():
    assert len(new_trace_id()) == 32
    assert len(new_span_id()) == 16


def test_llm_span_round_trips_through_json():
    span = root(
        kind=SpanKind.LLM,
        end_time=T0 + timedelta(seconds=2),
        model="claude-sonnet-5-5",
        input=[{"role": "user", "content": "hi"}],
        output="hello",
        input_tokens=10,
        output_tokens=3,
        trace=TraceMetadata(session_id="s1", user_id="u1", tags=["prod"]),
    )
    assert Span.model_validate_json(span.model_dump_json()) == span


def test_trace_metadata_only_on_root():
    with pytest.raises(ValidationError, match="root span only"):
        root(parent_span_id=new_span_id(), trace=TraceMetadata(user_id="u1"))


def test_rejects_end_before_start():
    with pytest.raises(ValidationError, match="before start_time"):
        root(end_time=T0 - timedelta(seconds=1))


def test_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        root(kind="mcp")


def test_rejects_malformed_ids():
    with pytest.raises(ValidationError):
        Span(trace_id="abc", span_id=new_span_id(), name="x", kind="tool", start_time=T0)
