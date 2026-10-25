"""`span()` and `@trace`: record units of work as spans, nested through a context variable.

A span is exported once, when it ends (never on start). Nesting follows `contextvars`, so it
works across threads started with a copied context and across asyncio tasks.
"""

import functools
import inspect
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, overload

from agenttrace._json import to_jsonable
from agenttrace.exporter import get_exporter
from agenttrace.spans import Span, SpanKind, SpanStatus, TraceMetadata, new_span_id, new_trace_id

log = logging.getLogger("agenttrace")

_current: ContextVar["ActiveSpan | None"] = ContextVar("agenttrace_current_span", default=None)


class ActiveSpan:
    """A span that hasn't ended. Set its output and fields while it runs."""

    def __init__(
        self,
        name: str,
        kind: SpanKind,
        parent: "ActiveSpan | None",
        input: Any = None,
        trace_metadata: TraceMetadata | None = None,
        attributes: dict[str, str] | None = None,
    ):
        self.name = name
        self.kind = SpanKind(kind)
        self.trace_id = parent.trace_id if parent else new_trace_id()
        self.span_id = new_span_id()
        self.parent_span_id = parent.span_id if parent else None
        self.start_time = datetime.now(UTC)
        self.input = to_jsonable(input)
        self.output: Any = None
        self.status = SpanStatus.OK
        self.error_type: str | None = None
        self.model: str | None = None
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.attributes: dict[str, str] = dict(attributes or {})
        # Trace metadata belongs on the root span only; on a child it's ignored.
        self.trace_metadata = trace_metadata if parent is None else None

    def set_input(self, value: Any) -> None:
        self.input = to_jsonable(value)

    def set_output(self, value: Any) -> None:
        self.output = to_jsonable(value)

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value if isinstance(value, str) else str(value)

    def set_error(self, error_type: str) -> None:
        self.status = SpanStatus.ERROR
        self.error_type = error_type

    def set_llm(
        self,
        model: str | None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def _end(self) -> None:
        exporter = get_exporter()
        if exporter is None:
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
                attributes=self.attributes,
                trace=self.trace_metadata,
            )
        except Exception:
            log.warning("agenttrace: could not build span %r", self.name, exc_info=True)
            return
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
    active = ActiveSpan(name, SpanKind(kind), _current.get(), input, meta, attributes)
    token = _current.set(active)
    try:
        yield active
    except BaseException as e:
        active.set_error(type(e).__name__)
        raise
    finally:
        _current.reset(token)
        active._end()


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

    def decorate(f: Callable) -> Callable:
        span_name = name or f.__qualname__

        if inspect.iscoroutinefunction(f):

            @functools.wraps(f)
            async def async_wrapper(*args, **kwargs):
                with span(span_name, kind, _call_input(f, args, kwargs), **span_options) as s:
                    result = await f(*args, **kwargs)
                    s.set_output(result)
                    return result

            return async_wrapper

        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            with span(span_name, kind, _call_input(f, args, kwargs), **span_options) as s:
                result = f(*args, **kwargs)
                s.set_output(result)
                return result

        return wrapper

    return decorate(fn) if fn is not None else decorate
