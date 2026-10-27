"""Record Anthropic `messages.create` calls as LLM spans.

Patches the sync and async `Messages` and beta `Messages` resources. Streaming calls
(`stream=True`) pass through untraced for now. The span's input is the whole request and its
output the whole response, so a replay can answer the call from its recording (ADR-0039).
"""

import functools
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from agenttrace.spans import SpanKind
from agenttrace.tracing import ActiveSpan, span

log = logging.getLogger("agenttrace")

SPAN_NAME = "anthropic.messages.create"
# How the request is sent, not what it asks: left out of the recorded input.
TRANSPORT_KEYS = frozenset({"extra_headers", "extra_query", "extra_body", "timeout", "stream"})
# Recorded first, so an offloaded input's 1 KB preview shows the conversation, not tool schemas.
LEADING_KEYS = ("system", "messages")


def instrument_anthropic() -> None:
    """Patch the installed `anthropic` package. Safe to call more than once."""
    try:
        from anthropic.resources.beta.messages import messages as beta_messages
        from anthropic.resources.messages import messages
    except ImportError:
        log.warning("agenttrace: anthropic is not installed; nothing to instrument")
        return
    for cls in (messages.Messages, beta_messages.Messages):
        _patch(cls, is_async=False)
    for cls in (messages.AsyncMessages, beta_messages.AsyncMessages):
        _patch(cls, is_async=True)


def _patch(cls: type, is_async: bool) -> None:
    original = cls.create
    if getattr(original, "_agenttrace_wrapped", False):
        return

    @functools.wraps(original)
    async def async_create(self, *args, **kwargs):
        if kwargs.get("stream"):
            return await original(self, *args, **kwargs)
        with _llm_span(kwargs) as s:
            response = await original(self, *args, **kwargs)
            _record_response(s, response)
            return response

    @functools.wraps(original)
    def sync_create(self, *args, **kwargs):
        if kwargs.get("stream"):
            return original(self, *args, **kwargs)
        with _llm_span(kwargs) as s:
            response = original(self, *args, **kwargs)
            _record_response(s, response)
            return response

    create = async_create if is_async else sync_create
    create._agenttrace_wrapped = True  # type: ignore[attr-defined]
    cls.create = create  # type: ignore[method-assign]


@contextmanager
def _llm_span(kwargs: dict[str, Any]) -> Iterator[ActiveSpan]:
    ordered = {k: kwargs[k] for k in LEADING_KEYS if k in kwargs} | kwargs
    request = {k: v for k, v in ordered.items() if k not in TRANSPORT_KEYS and _given(v)}
    attributes = {}
    if tools := kwargs.get("tools"):
        attributes["llm.tools"] = ",".join(str(t.get("name", "?")) for t in tools)
    with span(SPAN_NAME, SpanKind.LLM, request, attributes=attributes) as s:
        s.set_llm(kwargs.get("model"))  # the requested model, so a failed call still names it
        yield s


def _given(value: Any) -> bool:
    """False for the SDK's `NOT_GIVEN` and `omit` sentinels, which mean "not in the request"."""
    return type(value).__name__ not in ("NotGiven", "Omit")


def _cache_writes(usage: Any) -> tuple[int | None, int | None]:
    """(5-minute, 1-hour) cache-write tokens. They're priced differently, so use the per-TTL
    breakdown when present; without it, all writes are the default 5-minute TTL."""
    total = getattr(usage, "cache_creation_input_tokens", None)
    by_ttl = getattr(usage, "cache_creation", None)
    if by_ttl is not None:
        return (
            getattr(by_ttl, "ephemeral_5m_input_tokens", None),
            getattr(by_ttl, "ephemeral_1h_input_tokens", None),
        )
    return total, None


def _record_response(s: ActiveSpan, response: Any) -> None:
    usage = getattr(response, "usage", None)
    # response.model is the model that served the call, which differs from the request on a
    # fallback; cost must follow the model that actually ran.
    write_5m, write_1h = _cache_writes(usage)
    s.set_llm(
        getattr(response, "model", None) or s.model,
        getattr(usage, "input_tokens", None),
        getattr(usage, "output_tokens", None),
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", None),
        cache_write_5m_tokens=write_5m,
        cache_write_1h_tokens=write_1h,
    )
    if stop_reason := getattr(response, "stop_reason", None):
        s.set_attribute("llm.stop_reason", stop_reason)
    s.set_output(response)
