"""The trace filter: which traces the trace list shows and the overview counts, so a chart and the
list it links to agree (ADR-0038). `matches` is what a filter means; `trace_store` turns it into
SQL, and the integration tests hold the two to the same answers."""

from datetime import datetime
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from trayce.eval.results import EVAL_TAG
from trayce_ingest.traces import SpanKind, SpanRow, TraceStatus, TraceSummary

MAX_TAGS = 20
MAX_TEXT = 256


def _aware(value: datetime | None) -> datetime | None:
    # A naive time would be read in the server's zone, which no client can see.
    if value is not None and value.tzinfo is None:
        raise ValueError("give a time zone, such as Z or +02:00")
    return value


Time = Annotated[datetime | None, AfterValidator(_aware)]
FilterText = Annotated[str | None, Field(min_length=1, max_length=MAX_TEXT)]


class TraceFilter(BaseModel):
    """Every condition is optional, and all of them must hold. By default only production
    traffic matches: eval samples' traces and replay runs are left out (GLOSSARY.md)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    since: Time = None  # the trace started at or after this
    until: Time = None  # and before this
    status: TraceStatus | None = None
    tag: Annotated[list[str], Field(max_length=MAX_TAGS)] = []  # carries every one of these
    user_id: FilterText = None
    service_name: FilterText = None
    show_evals: bool = False
    show_replays: bool = False
    # The trace has a span of this kind, name, and model, errored when `span_error`.
    span_kind: SpanKind | None = None
    span_name: FilterText = None
    span_model: FilterText = None
    span_error: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.since and self.until and self.since >= self.until:
            raise ValueError("since must be before until")
        if self.span_error and not (self.span_kind or self.span_name or self.span_model):
            raise ValueError("span_error needs span_kind, span_name, or span_model")
        return self

    @property
    def matches_spans(self) -> bool:
        return bool(self.span_kind or self.span_name or self.span_model or self.span_error)


def matches(f: TraceFilter, trace: TraceSummary, spans: list[SpanRow]) -> bool:
    """Whether a trace, its status decided as the trace list decides it, passes the filter."""
    return (
        (f.since is None or trace.start_time >= f.since)
        and (f.until is None or trace.start_time < f.until)
        and (f.status is None or trace.status == f.status)
        and all(t in trace.tags for t in f.tag)
        and (f.user_id is None or trace.user_id == f.user_id)
        and (f.service_name is None or trace.service_name == f.service_name)
        and (f.show_evals or EVAL_TAG not in trace.tags)
        and (f.show_replays or trace.replay_of == "")
        and (not f.matches_spans or any(_span_matches(f, s) for s in spans))
    )


def _span_matches(f: TraceFilter, s: SpanRow) -> bool:
    return (
        (f.span_kind is None or s.kind == f.span_kind)
        and (f.span_name is None or s.name == f.span_name)
        and (f.span_model is None or s.model == f.span_model)
        and (not f.span_error or s.status == "error")
    )
