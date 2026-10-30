"""Trayce Python SDK: trace agent runs and send them to a Trayce server."""

from trayce._playback import RecordedToolError
from trayce.exporter import configure, flush
from trayce.integrations.anthropic import instrument_anthropic
from trayce.spans import Span, SpanKind, SpanStatus, TraceMetadata, new_span_id, new_trace_id
from trayce.tracing import (
    ActiveSpan,
    add_span_processor,
    call_tool,
    current_span,
    span,
    trace,
)

__all__ = [
    "ActiveSpan",
    "RecordedToolError",
    "Span",
    "SpanKind",
    "SpanStatus",
    "TraceMetadata",
    "add_span_processor",
    "call_tool",
    "configure",
    "current_span",
    "flush",
    "instrument_anthropic",
    "new_span_id",
    "new_trace_id",
    "span",
    "trace",
]
