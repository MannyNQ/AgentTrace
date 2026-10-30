"""Record Anthropic `messages.create` calls as LLM spans.

Patches the sync and async `Messages` and beta `Messages` resources. Streaming calls
(`stream=True`) pass through untraced for now. The span's input is the whole request and its
output the whole response, so a replay can answer the call from its recording (ADR-0039): under
a fork's playback, a call in the stubbed prefix returns the recorded message and never reaches
the API (ADR-0040).
"""

import functools
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from trayce import _playback
from trayce.spans import SpanKind
from trayce.tracing import INTERCEPTOR_ATTRIBUTE, ActiveSpan, span

log = logging.getLogger("trayce")

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
        log.warning("trayce: anthropic is not installed; nothing to instrument")
        return
    from anthropic.types import Message
    from anthropic.types.beta import BetaMessage

    _patch(messages.Messages, Message, is_async=False)
    _patch(beta_messages.Messages, BetaMessage, is_async=False)
    _patch(messages.AsyncMessages, Message, is_async=True)
    _patch(beta_messages.AsyncMessages, BetaMessage, is_async=True)


def _patch(cls: type, message: Any, is_async: bool) -> None:
    """`message` is the response model the resource returns, to rebuild a stubbed reply."""
    original = cls.create
    if getattr(original, "_trayce_wrapped", False):
        return

    @functools.wraps(original)
    async def async_create(self, *args, **kwargs):
        if kwargs.get("stream"):
            return await original(self, *args, **kwargs)
        with _llm_span(kwargs) as s:
            if (stub := _stub(s, kwargs, message)) is not None:
                return stub
            response = await original(self, *args, **kwargs)
            _record_response(s, response)
            return response

    @functools.wraps(original)
    def sync_create(self, *args, **kwargs):
        if kwargs.get("stream"):
            return original(self, *args, **kwargs)
        with _llm_span(kwargs) as s:
            if (stub := _stub(s, kwargs, message)) is not None:
                return stub
            response = original(self, *args, **kwargs)
            _record_response(s, response)
            return response

    create = async_create if is_async else sync_create
    create._trayce_wrapped = True  # type: ignore[attr-defined]
    cls.create = create  # type: ignore[method-assign]


def _request(kwargs: dict[str, Any]) -> dict[str, Any]:
    """The request as recorded: what it asks, system and messages first."""
    ordered = {k: kwargs[k] for k in LEADING_KEYS if k in kwargs} | kwargs
    return {k: v for k, v in ordered.items() if k not in TRANSPORT_KEYS and _given(v)}


def _stub(s: ActiveSpan, kwargs: dict[str, Any], message: Any) -> Any:
    """The recorded reply under a replay's playback, or None to call the API. A stubbed call
    records zero tokens, so it prices at $0 rather than as an unpriced call: nothing was billed."""
    p = _playback.current()
    recorded = p.answer_llm(_request(kwargs)) if p is not None else None
    if recorded is None:
        return None
    response = message.model_validate(recorded)
    s.set_llm(getattr(response, "model", None) or s.model, 0, 0, 0, 0, 0)
    s.set_attribute(_playback.STUBBED_ATTRIBUTE, "true")
    if stop_reason := getattr(response, "stop_reason", None):
        s.set_attribute("llm.stop_reason", stop_reason)
    s.set_output(response)
    return response


@contextmanager
def _llm_span(kwargs: dict[str, Any]) -> Iterator[ActiveSpan]:
    request = _request(kwargs)
    attributes = {INTERCEPTOR_ATTRIBUTE: "anthropic"}
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
