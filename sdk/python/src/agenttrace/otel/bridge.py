"""The OTel bridge: OpenTelemetry spans become spans in-process, and the SDK shares
OpenTelemetry's context (ADR-0036). Needs the `agenttrace[otel]` extra.

The eval runner and `agenttrace replay` install it whenever OpenTelemetry's SDK is importable,
so an OTel-instrumented agent's tool spans reach the trajectory capture (ADR-0022).
"""

import logging
import threading
from collections.abc import Callable
from typing import Literal

from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.trace import NonRecordingSpan, SpanContext, StatusCode, TraceFlags

from agenttrace import tracing
from agenttrace.otel.mapping import OtelEvent, OtelSpan, Unstorable, translate
from agenttrace.spans import SpanKind

log = logging.getLogger("agenttrace")

_STATUS: dict[StatusCode, Literal["ok", "error"]] = {StatusCode.OK: "ok", StatusCode.ERROR: "error"}
_lock = threading.Lock()
_installed: set[int] = set()  # ids of the providers our processor is on


class OtelContext:
    """OpenTelemetry's context, offered to `agenttrace.tracing`."""

    def current(self) -> tuple[str, str] | None:
        ctx = otel_trace.get_current_span().get_span_context()
        if not ctx.is_valid:
            return None
        return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")

    def enter(self, trace_id: str, span_id: str) -> Callable[[], None]:
        # Sampled, so OpenTelemetry spans under ours record under the default parent-based sampler.
        ctx = SpanContext(
            int(trace_id, 16), int(span_id, 16), is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )  # fmt: skip
        token = otel_context.attach(otel_trace.set_span_in_context(NonRecordingSpan(ctx)))
        return lambda: otel_context.detach(token)


class Bridge(SpanProcessor):
    """Translates each OpenTelemetry span as it ends and emits it like an SDK span."""

    def on_end(self, span: ReadableSpan) -> None:
        if tracing._otel is None:  # not installed, or reset by a test
            return
        try:
            s = translate(otel_span(span))
        except Unstorable as e:
            log.debug("agenttrace: dropped OpenTelemetry span %r: %s", span.name, e)
            return
        # The same call recorded by two instrumentations, such as the Anthropic SDK's own span
        # inside `instrument_anthropic()`'s: keeping both would count its cost twice.
        if s.kind == SpanKind.LLM and s.parent_span_id in tracing.open_llm_spans:
            return
        tracing.emit(s)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def otel_span(span: ReadableSpan) -> OtelSpan:
    ctx, parent = span.get_span_context(), span.parent
    assert ctx is not None  # an SDK span always has one
    return OtelSpan(
        trace_id=format(ctx.trace_id, "032x"),
        span_id=format(ctx.span_id, "016x"),
        parent_span_id=format(parent.span_id, "016x") if parent else None,
        parent_is_remote=bool(parent and parent.is_remote),
        name=span.name,
        start_time_ns=span.start_time or 0,
        end_time_ns=span.end_time or 0,
        status=_STATUS.get(span.status.status_code, "unset"),
        attributes=dict(span.attributes or {}),
        events=[OtelEvent(e.name, dict(e.attributes or {})) for e in span.events],
        resource=dict(span.resource.attributes),
    )


def install(provider: TracerProvider | None = None) -> None:
    """Bridge `provider`, else the global tracer provider, setting an SDK one when there is
    none, and share OpenTelemetry's context. Safe to call more than once."""
    with _lock:
        if provider is None:
            current = otel_trace.get_tracer_provider()
            if isinstance(current, TracerProvider):
                provider = current
            else:
                provider = TracerProvider()
                otel_trace.set_tracer_provider(provider)
        if id(provider) not in _installed:
            provider.add_span_processor(Bridge())
            _installed.add(id(provider))
        tracing.share_context(OtelContext())
