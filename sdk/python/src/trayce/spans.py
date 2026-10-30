"""The span contract shared by the SDK and the ingest service. Terms follow GLOSSARY.md."""

import secrets
from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, Field, model_validator

TRACE_ID_PATTERN = r"^[0-9a-f]{32}$"
SPAN_ID_PATTERN = r"^[0-9a-f]{16}$"


def new_trace_id() -> str:
    """16-byte hex, OTel format (ADR-0003)."""
    return secrets.token_hex(16)


def new_span_id() -> str:
    """8-byte hex, OTel format (ADR-0003)."""
    return secrets.token_hex(8)


class SpanKind(StrEnum):
    LLM = "llm"
    TOOL = "tool"
    AGENT = "agent"
    RETRIEVAL = "retrieval"
    CUSTOM = "custom"


class SpanStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class TraceMetadata(BaseModel):
    """Trace-level fields. Only the root span carries them."""

    session_id: str | None = None
    user_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)


class Span(BaseModel):
    trace_id: str = Field(pattern=TRACE_ID_PATTERN)
    span_id: str = Field(pattern=SPAN_ID_PATTERN)
    parent_span_id: str | None = Field(default=None, pattern=SPAN_ID_PATTERN)
    name: str = Field(min_length=1)
    kind: SpanKind
    start_time: datetime
    end_time: datetime | None = None
    status: SpanStatus = SpanStatus.OK
    error_type: str | None = None
    input: Any = None
    output: Any = None
    # LLM span fields. cost_usd is not here: the worker computes it at ingestion (ADR-0010).
    model: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    # Prompt-cache tokens, priced separately from input_tokens (which excludes them).
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_5m_tokens: int | None = Field(default=None, ge=0)
    cache_write_1h_tokens: int | None = Field(default=None, ge=0)
    attributes: dict[str, str] = Field(default_factory=dict)
    trace: TraceMetadata | None = None

    @property
    def is_root(self) -> bool:
        return self.parent_span_id is None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.trace is not None and not self.is_root:
            raise ValueError("trace metadata belongs on the root span only")
        if self.end_time is not None and self.end_time < self.start_time:
            raise ValueError("end_time is before start_time")
        if self.parent_span_id == self.span_id:
            raise ValueError("a span cannot be its own parent")
        return self
