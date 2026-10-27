"""Translate one OpenTelemetry span into a `Span` (ADR-0035).

Two conventions are read: OpenInference, when a span sets `openinference.span.kind`, else
OpenTelemetry's GenAI conventions. The GenAI ones are still at Development status and renaming
attributes, so old and new names are both read. This module is the only place convention
attribute names appear.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import ValidationError

from agenttrace._json import to_jsonable
from agenttrace.spans import Span, SpanKind, SpanStatus, TraceMetadata

MAX_ATTRIBUTE_BYTES = 4096
CONVENTION_KEY = "agenttrace.convention"

_HEX = {32: re.compile(r"^[0-9a-f]{32}$"), 16: re.compile(r"^[0-9a-f]{16}$")}
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

GEN_AI_KINDS = {
    "chat": SpanKind.LLM,
    "text_completion": SpanKind.LLM,
    "generate_content": SpanKind.LLM,
    "embeddings": SpanKind.LLM,
    "execute_tool": SpanKind.TOOL,
    "invoke_agent": SpanKind.AGENT,
    "create_agent": SpanKind.AGENT,
    "invoke_workflow": SpanKind.AGENT,
    "plan": SpanKind.AGENT,
    "retrieval": SpanKind.RETRIEVAL,
}

# CHAIN stays custom: LangChain wraps nearly every step in a chain, and calling those agents
# would bury the real agent spans.
OPENINFERENCE_KINDS = {
    "LLM": SpanKind.LLM,
    "EMBEDDING": SpanKind.LLM,
    "TOOL": SpanKind.TOOL,
    "AGENT": SpanKind.AGENT,
    "RETRIEVER": SpanKind.RETRIEVAL,
    "RERANKER": SpanKind.RETRIEVAL,
}

# Each field's attribute names, the first present wins.
GEN_AI_MODEL = ("gen_ai.response.model", "gen_ai.request.model")


@dataclass(frozen=True)
class TokenKeys:
    input: tuple[str, ...]
    output: tuple[str, ...]
    cache_read: tuple[str, ...]
    cache_write: tuple[str, ...]


GEN_AI_TOKENS = TokenKeys(
    input=("gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens"),
    output=("gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens"),
    cache_read=("gen_ai.usage.cache_read.input_tokens", "gen_ai.usage.cache_read_input_tokens"),
    cache_write=(
        "gen_ai.usage.cache_write.input_tokens",
        "gen_ai.usage.cache_creation.input_tokens",
        "gen_ai.usage.cache_creation_input_tokens",
    ),
)
OI_TOKENS = TokenKeys(
    input=("llm.token_count.prompt",),
    output=("llm.token_count.completion",),
    cache_read=("llm.token_count.prompt_details.cache_read",),
    cache_write=("llm.token_count.prompt_details.cache_write",),
)

SESSION_ID = ("gen_ai.conversation.id", "session.id")
USER_ID = ("user.id", "enduser.id")
TAGS = "tag.tags"
METADATA = "metadata"


@dataclass(frozen=True)
class OtelEvent:
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OtelSpan:
    """An OpenTelemetry span as either source decodes it. IDs are hex; times are Unix
    nanoseconds, with 0 meaning unset, as in OTLP."""

    trace_id: str
    span_id: str
    name: str
    start_time_ns: int
    end_time_ns: int = 0
    parent_span_id: str | None = None
    parent_is_remote: bool = False
    status: Literal["unset", "ok", "error"] = "unset"
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[OtelEvent] = field(default_factory=list)
    resource: dict[str, Any] = field(default_factory=dict)


class Unstorable(ValueError):
    """The span can't be stored; the message says why. Callers drop it and report it."""


def translate(o: OtelSpan) -> Span:
    trace_id = _id(o.trace_id, 32, "trace ID")
    span_id = _id(o.span_id, 16, "span ID")
    if o.start_time_ns <= 0:
        raise Unstorable("no start time")
    if o.end_time_ns and o.end_time_ns < o.start_time_ns:
        raise Unstorable("end time is before start time")
    # A remote parent lives in another service that may never send it; without a root here the
    # trace would never complete and would show as error (ADR-0004).
    parent = None if o.parent_is_remote else _parent_id(o.parent_span_id)

    attrs = dict(o.attributes)
    convention, kind = _convention_and_kind(attrs)
    name = o.name or "span"
    fields: dict[str, Any] = {}

    if convention == "openinference":
        fields["input"] = _oi_value(attrs, "input")
        fields["output"] = _oi_value(attrs, "output")
        if kind == SpanKind.TOOL:
            name = str(attrs.get("tool.name") or name)
        if kind == SpanKind.LLM:
            model = attrs.get("llm.model_name")
            fields |= _tokens(attrs, OI_TOKENS)
            fields["model"] = str(model) if model is not None else None
    elif convention == "gen_ai":
        if kind == SpanKind.TOOL:
            # Trajectories read the tool from the span name (ADR-0022).
            name = str(attrs.get("gen_ai.tool.name") or name)
            fields["input"] = _json_or_text(attrs.pop("gen_ai.tool.call.arguments", None))
            fields["output"] = _json_or_text(attrs.pop("gen_ai.tool.call.result", None))
        else:
            system = _json_or_text(attrs.pop("gen_ai.system_instructions", None))
            messages = _json_or_text(attrs.pop("gen_ai.input.messages", None))
            if system is not None:
                fields["input"] = {"system": system, "messages": messages}
            else:
                fields["input"] = messages
            fields["output"] = _json_or_text(attrs.pop("gen_ai.output.messages", None))
        # Agent spans often total their children's usage; only LLM spans carry LLM fields,
        # so nothing is counted twice.
        if kind == SpanKind.LLM:
            model = _first(attrs, GEN_AI_MODEL)
            fields |= _tokens(attrs, GEN_AI_TOKENS)
            fields["model"] = str(model) if model is not None else None

    trace = _trace_metadata(attrs) if parent is None else None
    attributes = {k: _attribute(v) for k, v in attrs.items()}
    if parent is None:
        attributes |= {f"resource.{k}": _attribute(v) for k, v in o.resource.items()}
    attributes[CONVENTION_KEY] = convention

    status = SpanStatus.ERROR if o.status == "error" else SpanStatus.OK
    try:
        return Span(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent,
            name=name,
            kind=kind,
            start_time=_time(o.start_time_ns),
            end_time=_time(o.end_time_ns) if o.end_time_ns else None,
            status=status,
            error_type=_error_type(o) if status == SpanStatus.ERROR else None,
            attributes=attributes,
            trace=trace,
            **fields,
        )
    except ValidationError as e:
        raise Unstorable(e.errors(include_url=False, include_input=False)[0]["msg"]) from e


def _convention_and_kind(attrs: dict[str, Any]) -> tuple[str, SpanKind]:
    if (oi_kind := attrs.get("openinference.span.kind")) is not None:
        return "openinference", OPENINFERENCE_KINDS.get(str(oi_kind).upper(), SpanKind.CUSTOM)
    if (op := attrs.get("gen_ai.operation.name")) is not None:
        return "gen_ai", GEN_AI_KINDS.get(str(op), SpanKind.CUSTOM)
    if any(k.startswith("gen_ai.") for k in attrs):
        return "gen_ai", SpanKind.CUSTOM
    return "none", SpanKind.CUSTOM


def _id(value: str, length: int, what: str) -> str:
    v = (value or "").lower()
    if not _HEX[length].match(v) or v == "0" * length:
        raise Unstorable(f"invalid {what} {value!r}")
    return v


def _parent_id(value: str | None) -> str | None:
    v = (value or "").lower()
    if not v or v == "0" * 16:
        return None
    return _id(v, 16, "parent span ID")


def _time(ns: int) -> datetime:
    return _EPOCH + timedelta(microseconds=ns // 1000)


def _first(attrs: dict[str, Any], keys: tuple[str, ...]) -> Any:
    return next((attrs[k] for k in keys if attrs.get(k) is not None), None)


def _int(value: Any) -> int | None:
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _tokens(attrs: dict[str, Any], keys: TokenKeys) -> dict[str, int | None]:
    """Both conventions count cached tokens inside input tokens; ours exclude them (ADR-0010).
    No convention says which TTL a cache write used, so all are the default 5 minutes."""
    reported = _int(_first(attrs, keys.input))
    read = _int(_first(attrs, keys.cache_read))
    write = _int(_first(attrs, keys.cache_write))
    uncached = None if reported is None else max(0, reported - (read or 0) - (write or 0))
    return {
        "input_tokens": uncached,
        "output_tokens": _int(_first(attrs, keys.output)),
        "cache_read_tokens": read,
        "cache_write_5m_tokens": write,
    }


def _json_or_text(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return to_jsonable(value)


def _oi_value(attrs: dict[str, Any], prefix: str) -> Any:
    """OpenInference's `input.value` or `output.value`. Its mime type says whether it's JSON;
    without one, JSON is parsed and anything else stays text."""
    value = attrs.pop(f"{prefix}.value", None)
    mime = attrs.get(f"{prefix}.mime_type")
    if isinstance(value, str) and mime is not None and mime != "application/json":
        return value
    return _json_or_text(value)


def _trace_metadata(attrs: dict[str, Any]) -> TraceMetadata | None:
    session_id = _first(attrs, SESSION_ID)
    user_id = _first(attrs, USER_ID)
    tags = _json_or_text(attrs.get(TAGS))
    metadata = _json_or_text(attrs.get(METADATA))
    tags = [str(t) for t in tags] if isinstance(tags, list) else []
    metadata = (
        {str(k): v if isinstance(v, str) else json.dumps(v) for k, v in metadata.items()}
        if isinstance(metadata, dict)
        else {}
    )
    if session_id is None and user_id is None and not tags and not metadata:
        return None
    return TraceMetadata(
        session_id=None if session_id is None else str(session_id),
        user_id=None if user_id is None else str(user_id),
        tags=tags,
        metadata=metadata,
    )


def _error_type(o: OtelSpan) -> str | None:
    if (t := o.attributes.get("error.type")) is not None:
        return str(t)
    for e in o.events:
        if e.name == "exception" and e.attributes.get("exception.type") is not None:
            return str(e.attributes["exception.type"])
    return None


def _attribute(value: Any) -> str:
    """Span attributes are strings, cut at 4 KB so a large one can't bloat ClickHouse rows."""
    if isinstance(value, str):
        text = value
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int | float):
        text = str(value)
    elif isinstance(value, bytes):
        text = value.hex()
    else:
        text = json.dumps(to_jsonable(value))
    data = text.encode()
    if len(data) <= MAX_ATTRIBUTE_BYTES:
        return text
    return data[:MAX_ATTRIBUTE_BYTES].decode("utf-8", errors="ignore")
