"""`span()` and `@trace`: record units of work as spans, nested through a context variable.

A span is exported once, when it ends (never on start). Nesting follows `contextvars`, so it
works across threads started with a copied context and across asyncio tasks. Span processors see
every span as it ends, whether or not an exporter is configured: the eval runner reads each
sample's trajectory this way (ADR-0022).

Once the OTel bridge is installed (ADR-0036), OpenTelemetry's context is the SDK's too: a span's
parent is whichever span is current, ours or OpenTelemetry's, and ours are current for
OpenTelemetry's.
"""

import functools
import inspect
import logging
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Protocol, overload

from agenttrace import _playback
from agenttrace._json import is_lossless, to_jsonable
from agenttrace._quiet import untraced as untraced  # public here, as before
from agenttrace._quiet import untraced_now
from agenttrace.exporter import get_exporter
from agenttrace.spans import Span, SpanKind, SpanStatus, TraceMetadata, new_span_id, new_trace_id

log = logging.getLogger("agenttrace")

_current: ContextVar["ActiveSpan | None"] = ContextVar("agenttrace_current_span", default=None)

SERVICE_NAME_ATTRIBUTE = "resource.service.name"
# On tool spans, how the output was recorded: `json` exactly, `text` as a bare string (which the
# stored payload can't tell from JSON), or `lossy`. A fork stubs only the first two (ADR-0040).
OUTPUT_RECORDED_ATTRIBUTE = "agenttrace.output_recorded"
# Which SDK hook recorded the span, so a fork knows it can answer the call: `anthropic`,
# `call_tool`, or `trace`. Unset on `span()` blocks and bridged spans, which can't be stubbed.
INTERCEPTOR_ATTRIBUTE = "agenttrace.interceptor"
ERROR_MESSAGE_ATTRIBUTE = "agenttrace.error_message"
MAX_ERROR_MESSAGE = 1000


def service_name() -> str | None:
    """The service a root span belongs to: `AGENTTRACE_SERVICE_NAME`, else OpenTelemetry's
    `OTEL_SERVICE_NAME`, so one setting names an agent's traces over the SDK and OTLP alike."""
    return os.environ.get("AGENTTRACE_SERVICE_NAME") or os.environ.get("OTEL_SERVICE_NAME") or None


SpanProcessor = Callable[[Span], None]
_processors: tuple[SpanProcessor, ...] = ()
_processors_lock = threading.Lock()


class SharedContext(Protocol):
    """OpenTelemetry's context, as the bridge offers it (agenttrace.otel.bridge)."""

    def current(self) -> tuple[str, str] | None:
        """The current span's (trace_id, span_id), if there is one."""
        ...

    def enter(self, trace_id: str, span_id: str) -> Callable[[], None]:
        """Make this span current; returns the function that undoes it."""
        ...


_otel: SharedContext | None = None  # set by the bridge
# LLM spans that haven't ended, while the bridge is installed: an OpenTelemetry LLM span
# directly under one is the same call recorded twice (agenttrace.otel.bridge).
open_llm_spans: set[str] = set()


def share_context(ctx: SharedContext) -> None:
    global _otel
    _otel = ctx


def add_span_processor(processor: SpanProcessor) -> Callable[[], None]:
    """Call `processor` with every span as it ends, on the thread that ended it. Returns a
    function that removes it. A processor that raises is logged and doesn't affect the span."""
    global _processors
    with _processors_lock:
        _processors = (*_processors, processor)

    def remove() -> None:
        global _processors
        with _processors_lock:
            _processors = tuple(p for p in _processors if p is not processor)

    return remove


class ActiveSpan:
    """A span that hasn't ended. Set its output and fields while it runs."""

    def __init__(
        self,
        name: str,
        kind: SpanKind,
        parent: tuple[str, str] | None,
        input: Any = None,
        trace_metadata: TraceMetadata | None = None,
        attributes: dict[str, str] | None = None,
    ):
        self.name = name
        self.kind = SpanKind(kind)
        self.trace_id = parent[0] if parent else new_trace_id()
        self.span_id = new_span_id()
        self.parent_span_id = parent[1] if parent else None
        self.start_time = datetime.now(UTC)
        self.input = to_jsonable(input)
        self.output: Any = None
        self.status = SpanStatus.OK
        self.error_type: str | None = None
        self.model: str | None = None
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.cache_read_tokens: int | None = None
        self.cache_write_5m_tokens: int | None = None
        self.cache_write_1h_tokens: int | None = None
        self.attributes: dict[str, str] = dict(attributes or {})
        if parent is None and (service := service_name()):
            # The key OTLP ingest gives a resource's service.name, so traces_mv reads one key.
            self.attributes.setdefault(SERVICE_NAME_ATTRIBUTE, service)
        # Trace metadata belongs on the root span only; on a child it's ignored.
        self.trace_metadata = trace_metadata if parent is None else None

    def set_input(self, value: Any) -> None:
        self.input = to_jsonable(value)

    def set_output(self, value: Any) -> None:
        self.output = to_jsonable(value)
        if self.kind == SpanKind.TOOL:
            self.attributes[OUTPUT_RECORDED_ATTRIBUTE] = _recorded(value)

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value if isinstance(value, str) else str(value)

    def set_error(self, error_type: str, message: str | None = None) -> None:
        self.status = SpanStatus.ERROR
        self.error_type = error_type
        if message:
            self.attributes[ERROR_MESSAGE_ATTRIBUTE] = message[:MAX_ERROR_MESSAGE]

    def set_llm(
        self,
        model: str | None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cache_read_tokens: int | None = None,
        cache_write_5m_tokens: int | None = None,
        cache_write_1h_tokens: int | None = None,
    ) -> None:
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = cache_read_tokens
        self.cache_write_5m_tokens = cache_write_5m_tokens
        self.cache_write_1h_tokens = cache_write_1h_tokens

    def _end(self) -> None:
        if not _wanted():
            return
        try:
            span = Span(
                trace_id=self.trace_id,
                span_id=self.span_id,
                parent_span_id=self.parent_span_id,
                name=self.name,
                kind=self.kind,
                start_time=self.start_time,
                end_time=datetime.now(UTC),
                status=self.status,
                error_type=self.error_type,
                input=self.input,
                output=self.output,
                model=self.model,
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                cache_read_tokens=self.cache_read_tokens,
                cache_write_5m_tokens=self.cache_write_5m_tokens,
                cache_write_1h_tokens=self.cache_write_1h_tokens,
                attributes=self.attributes,
                trace=self.trace_metadata,
            )
        except Exception:
            log.warning("agenttrace: could not build span %r", self.name, exc_info=True)
            return
        emit(span)


def _wanted() -> bool:
    return (get_exporter() is not None or bool(_processors)) and not untraced_now.get()


def emit(span: Span) -> None:
    """Hand an ended span to the processors and the exporter, unless it's untraced. The bridge
    sends translated OpenTelemetry spans this way."""
    if not _wanted():
        return
    exporter = get_exporter()
    for processor in _processors:
        try:
            processor(span)
        except Exception:
            log.warning("agenttrace: span processor %r failed", processor, exc_info=True)
    if exporter is not None:
        exporter.export(span)


def current_span() -> ActiveSpan | None:
    return _current.get()


@contextmanager
def span(
    name: str,
    kind: SpanKind | str = SpanKind.CUSTOM,
    input: Any = None,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, str] | None = None,
    attributes: dict[str, str] | None = None,
) -> Iterator[ActiveSpan]:
    """Record the enclosed block as a span. An exception marks it `error` and propagates.

    `session_id`, `user_id`, `tags` and `metadata` are trace metadata, kept on the root span only.
    """
    meta = None
    if session_id or user_id or tags or metadata:
        meta = TraceMetadata(
            session_id=session_id, user_id=user_id, tags=tags or [], metadata=metadata or {}
        )
    otel = _otel
    parent = otel.current() if otel else None
    if parent is None and (p := _current.get()) is not None:
        parent = (p.trace_id, p.span_id)
    active = ActiveSpan(name, SpanKind(kind), parent, input, meta, attributes)
    token = _current.set(active)
    leave = otel.enter(active.trace_id, active.span_id) if otel else None
    if otel and active.kind == SpanKind.LLM:
        open_llm_spans.add(active.span_id)
    try:
        yield active
    except BaseException as e:
        # Only a tool's message is kept: a fork re-raises it (ADR-0040). Elsewhere it can carry
        # URLs or driver errors nobody asked to store.
        # A stubbed call's error keeps the recorded class name, so the run reads like the original.
        error_type = getattr(e, "recorded_type", None) or type(e).__name__
        active.set_error(error_type, _message(e) if active.kind == SpanKind.TOOL else None)
        raise
    finally:
        if leave:
            leave()
        open_llm_spans.discard(active.span_id)
        _current.reset(token)
        active._end()


def _recorded(value: Any) -> str:
    if type(value) is str:
        return "text"
    return "json" if is_lossless(value) else "lossy"


def _message(e: BaseException) -> str | None:
    try:
        return str(e) or None
    except Exception:  # a broken __str__ mustn't replace the exception being raised
        return None


_CALL_TOOL = {INTERCEPTOR_ATTRIBUTE: "call_tool"}
_LIVE = object()


def _answer(s: ActiveSpan, name: str, args: Any) -> Any:
    """A tool call's recorded result under a replay's playback, or `_LIVE` to run it. A call
    that failed in the recording raises RecordedToolError (ADR-0040)."""
    p = _playback.current()
    rec = p.answer_tool(name, args) if p is not None else None
    if rec is None:
        return _LIVE
    s.set_attribute(_playback.STUBBED_ATTRIBUTE, "true")
    if rec.error_type is not None:
        raise _playback.RecordedToolError(rec.error_type, rec.error_message)
    return rec.output


def call_tool(name: str, args: dict[str, Any], fn: Callable[..., Any]) -> Any:
    """Call `fn(**args)` as the tool `name`, recorded as a tool span with `args` as its input.
    For dispatch loops that only know which tool to run at call time; a replay can answer the
    call from its recording (ADR-0040). An async `fn` returns an awaitable, as calling it would."""
    if inspect.iscoroutinefunction(fn) or inspect.iscoroutinefunction(type(fn).__call__):

        async def call_async() -> Any:
            with span(name, SpanKind.TOOL, args, attributes=_CALL_TOOL) as s:
                result = _answer(s, name, args)
                if result is _LIVE:
                    result = await fn(**args)
                s.set_output(result)
                return result

        return call_async()
    with span(name, SpanKind.TOOL, args, attributes=_CALL_TOOL) as s:
        result = _answer(s, name, args)
        if result is _LIVE:
            result = fn(**args)
        s.set_output(result)
        return result


def _call_input(fn: Callable, args: tuple, kwargs: dict) -> dict[str, Any]:
    try:
        bound = inspect.signature(fn).bind(*args, **kwargs)
    except (TypeError, ValueError):
        return {"args": args, "kwargs": kwargs}
    bound.apply_defaults()
    return {k: v for k, v in bound.arguments.items() if k not in ("self", "cls")}


@overload
def trace(fn: Callable, /) -> Callable: ...
@overload
def trace(
    *, name: str | None = None, kind: SpanKind | str = SpanKind.CUSTOM, **span_options: Any
) -> Callable[[Callable], Callable]: ...
def trace(
    fn: Callable | None = None,
    /,
    *,
    name: str | None = None,
    kind: SpanKind | str = SpanKind.CUSTOM,
    **span_options: Any,
):
    """Decorator: each call becomes a span, with the call's arguments as input and its return
    value as output. Works on sync and async functions. Use as `@trace` or `@trace(kind="agent")`.
    """

    tool = SpanKind(kind) == SpanKind.TOOL
    if tool:
        attributes = span_options.get("attributes") or {}
        span_options["attributes"] = {**attributes, INTERCEPTOR_ATTRIBUTE: "trace"}

    def decorate(f: Callable) -> Callable:
        span_name = name or f.__qualname__

        if inspect.iscoroutinefunction(f):

            @functools.wraps(f)
            async def async_wrapper(*args, **kwargs):
                inp = _call_input(f, args, kwargs)
                with span(span_name, kind, inp, **span_options) as s:
                    result = _answer(s, span_name, inp) if tool else _LIVE
                    if result is _LIVE:
                        result = await f(*args, **kwargs)
                    s.set_output(result)
                    return result

            return async_wrapper

        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            inp = _call_input(f, args, kwargs)
            with span(span_name, kind, inp, **span_options) as s:
                result = _answer(s, span_name, inp) if tool else _LIVE
                if result is _LIVE:
                    result = f(*args, **kwargs)
                s.set_output(result)
                return result

        return wrapper

    return decorate(fn) if fn is not None else decorate
