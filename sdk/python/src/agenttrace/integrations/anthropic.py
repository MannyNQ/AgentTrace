"""Record Anthropic `messages.create` calls as LLM spans.

Patches the sync and async `Messages` and beta `Messages` resources. Streaming calls
(`stream=True`) pass through untraced for now.
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
# Request parameters recorded as span attributes; everything else is input or too large.
PARAM_KEYS = ("max_tokens", "output_config", "thinking", "tool_choice", "betas", "fallbacks")


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
    request = {"system": kwargs.get("system"), "messages": kwargs.get("messages")}
    attributes = {k: str(kwargs[k]) for k in PARAM_KEYS if k in kwargs}
    if tools := kwargs.get("tools"):
        attributes["llm.tools"] = ",".join(str(t.get("name", "?")) for t in tools)
    with span(SPAN_NAME, SpanKind.LLM, request, attributes=attributes) as s:
        s.set_llm(kwargs.get("model"))  # the requested model, so a failed call still names it
        yield s


def _record_response(s: ActiveSpan, response: Any) -> None:
    usage = getattr(response, "usage", None)
    # response.model is the model that served the call, which differs from the request on a
    # fallback; cost must follow the model that actually ran.
    s.set_llm(
        getattr(response, "model", None) or s.model,
        getattr(usage, "input_tokens", None),
        getattr(usage, "output_tokens", None),
    )
    for key in ("cache_read_input_tokens", "cache_creation_input_tokens"):
        if (value := getattr(usage, key, None)) is not None:
            s.set_attribute(f"llm.usage.{key}", value)
    if stop_reason := getattr(response, "stop_reason", None):
        s.set_attribute("llm.stop_reason", stop_reason)
    s.set_output(getattr(response, "content", response))
