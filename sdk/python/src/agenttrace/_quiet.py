"""Keeping work out of every trace: the SDK's own requests to ingest, and the LLM judge.

The SDK's HTTP calls run `quiet()`, so they're dropped by the SDK (and the OTel bridge, which emits
through it) and, when OpenTelemetry is present, not recorded by its instrumentation either, as
OpenTelemetry's own exporters do. Otherwise, with urllib instrumented, each POST to ingest would be
a span the bridge exports with the next POST, forever.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

untraced_now: ContextVar[bool] = ContextVar("agenttrace_untraced", default=False)


@contextmanager
def untraced() -> Iterator[None]:
    """Spans ended inside the block are dropped: neither exported nor seen by processors."""
    token = untraced_now.set(True)
    try:
        yield
    finally:
        untraced_now.reset(token)


@contextmanager
def quiet() -> Iterator[None]:
    """`untraced()`, and OpenTelemetry instrumentation suppressed, for the SDK's own requests."""
    restore = _suppress_otel_instrumentation()
    try:
        with untraced():
            yield
    finally:
        restore()


def _suppress_otel_instrumentation() -> Callable[[], None]:
    try:
        from opentelemetry import context as otel_context

        key = otel_context._SUPPRESS_INSTRUMENTATION_KEY
    except (ImportError, AttributeError):
        return lambda: None
    token = otel_context.attach(otel_context.set_value(key, True))
    return lambda: otel_context.detach(token)
