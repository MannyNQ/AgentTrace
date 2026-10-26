"""Reading traces: the trace list, one trace with its spans, a span's full payload, and what a
trace did (ADR-0031). Trace completion and status are decided here, when a trace is read
(ADR-0013, GLOSSARY.md). Storage is behind `Store`; `trace_store` has the ClickHouse queries."""

from datetime import datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import UUID

from agenttrace.eval.results import ToolCall
from agenttrace_ingest.datasets import DatasetError, NotFound, Record
from agenttrace_ingest.trace_inputs import decode_payload

SpanKind = Literal["llm", "tool", "agent", "retrieval", "custom"]
TraceStatus = Literal["ok", "error", "running"]
Which = Literal["input", "output"]
MAX_PAGE = 200
MAX_LIVE_IDS = 5000


class Unavailable(DatasetError):
    """ClickHouse or object storage can't be read; `error` says which and why."""

    status = 503


class SummaryRow(Record):
    """One trace's merged aggregates, as `traces` stores them."""

    trace_id: str
    start_time: datetime
    last_end_time: datetime
    last_ingested_at: datetime
    span_count: int
    cost_usd: float
    llm_spans: int
    unpriced_llm_spans: int
    has_root: bool
    root_ended: bool
    root_error: bool
    root_end_time: datetime
    root_name: str
    session_id: str
    user_id: str
    tags: list[str]
    replay_of: str
    errors: int


class SpanRow(Record):
    """A span as stored, URIs included; the API never shows the URIs."""

    span_id: str
    parent_span_id: str
    name: str
    kind: SpanKind
    start_time: datetime
    end_time: datetime | None
    status: Literal["ok", "error"]
    error_type: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    cost_usd: float | None
    input_preview: str
    output_preview: str
    input_uri: str
    output_uri: str
    attributes: dict[str, str]


class TraceSummary(Record):
    trace_id: str
    start_time: datetime
    root_name: str
    status: TraceStatus
    latency_ms: float
    span_count: int
    cost_usd: float | None  # null when nothing in the trace was priced
    unpriced_llm_spans: int
    error_count: int
    tags: list[str]
    user_id: str
    session_id: str
    last_end_time: datetime
    replay_of: str  # the original trace when this is a replay run (ADR-0028), else ""


class Span(Record):
    span_id: str
    parent_span_id: str
    name: str
    kind: SpanKind
    start_time: datetime
    end_time: datetime | None
    status: Literal["ok", "error"]
    error_type: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    cost_usd: float | None
    input_preview: str
    output_preview: str
    # The full payload is in object storage, and the preview is its first 1 KB (ADR-0009).
    input_offloaded: bool
    output_offloaded: bool
    attributes: dict[str, str]


class Cursor(Record):
    start_time: datetime
    trace_id: str


class TraceList(Record):
    traces: list[TraceSummary]
    next: Cursor | None  # pass as `before_start` and `before_trace` for the next page


class TraceDetail(Record):
    summary: TraceSummary
    spans: list[Span]


class Payload(Record):
    text: str  # as stored: a string payload bare, anything else as JSON


class Outcome(Record):
    """What a trace did, in the shape a replay run records (sdk `agenttrace.eval.runner`): its
    tool calls in start order, and the root span's output."""

    trajectory: list[ToolCall]
    output: Any
    root_error: str | None


class LiveTraces(Record):
    trace_ids: list[str]


class Store(Protocol):
    """Every method raises Unavailable when its store can't be read."""

    def summaries(
        self, project: UUID, before: Cursor | None, show_replays: bool, limit: int
    ) -> list[SummaryRow]:
        """Newest first, strictly before `before`."""
        ...

    def summary(self, project: UUID, trace_id: str) -> SummaryRow | None: ...

    def spans(self, project: UUID, trace_id: str, start: datetime, end: datetime) -> list[SpanRow]:
        """The trace's spans with start times in [start, end], deduplicated, in start order."""
        ...

    def live(self, project: UUID, trace_ids: list[str]) -> set[str]: ...

    def payload(self, uri: str) -> str: ...


def summarize(r: SummaryRow, now: datetime, quiet_period: timedelta) -> TraceSummary:
    complete = r.root_ended or now - r.last_ingested_at > quiet_period
    status: TraceStatus = (
        "running" if not complete else "error" if r.root_error or not r.root_ended else "ok"
    )
    end = r.root_end_time if r.root_ended else r.last_end_time
    return TraceSummary(
        trace_id=r.trace_id,
        start_time=r.start_time,
        root_name=r.root_name or ("(unnamed)" if r.has_root else "(root span missing)"),
        status=status,
        latency_ms=(end - r.start_time).total_seconds() * 1000,
        span_count=r.span_count,
        cost_usd=r.cost_usd if r.llm_spans > r.unpriced_llm_spans else None,
        unpriced_llm_spans=r.unpriced_llm_spans,
        error_count=r.errors,
        tags=r.tags,
        user_id=r.user_id,
        session_id=r.session_id,
        last_end_time=r.last_end_time,
        replay_of=r.replay_of,
    )


def _span(r: SpanRow) -> Span:
    shown = r.model_dump(exclude={"input_uri", "output_uri"})
    return Span(**shown, input_offloaded=bool(r.input_uri), output_offloaded=bool(r.output_uri))


class Traces:
    def __init__(self, store: Store, quiet_period: timedelta, now):
        self.store, self.quiet_period, self.now = store, quiet_period, now

    def page(
        self, project: UUID, before: Cursor | None, show_replays: bool, limit: int
    ) -> TraceList:
        """Replay runs are hidden unless `show_replays` (ADR-0029)."""
        limit = max(1, min(limit, MAX_PAGE))
        rows = self.store.summaries(project, before, show_replays, limit + 1)
        page, now = rows[:limit], self.now()
        last = page[-1] if page else None
        next_ = (
            Cursor(start_time=last.start_time, trace_id=last.trace_id)
            if len(rows) > limit and last
            else None
        )
        return TraceList(traces=[summarize(r, now, self.quiet_period) for r in page], next=next_)

    def detail(self, project: UUID, trace_id: str) -> TraceDetail:
        summary, spans = self._read(project, trace_id)
        return TraceDetail(
            summary=summarize(summary, self.now(), self.quiet_period),
            spans=[_span(s) for s in spans],
        )

    def payload(self, project: UUID, trace_id: str, span_id: str, which: Which) -> Payload:
        """The span's full input or output. The URI comes from the span row, never from the
        request."""
        _, spans = self._read(project, trace_id)
        span = next((s for s in spans if s.span_id == span_id), None)
        if span is None:
            raise NotFound("span not found")
        return Payload(text=self._text(span, which))

    def outcome(self, project: UUID, trace_id: str) -> Outcome:
        _, spans = self._read(project, trace_id)
        trajectory = []
        for s in spans:
            if s.kind == "tool":
                args = decode_payload(self._text(s, "input"))
                trajectory.append(
                    ToolCall(
                        tool=s.name or "(unnamed)", args=args if isinstance(args, dict) else None
                    )
                )
        root = next((s for s in spans if s.parent_span_id == ""), None)
        return Outcome(
            trajectory=trajectory,
            output=decode_payload(self._text(root, "output")) if root else None,
            root_error=(root.error_type or "error") if root and root.status == "error" else None,
        )

    def live(self, project: UUID, trace_ids: list[str]) -> LiveTraces:
        """Which of these traces still exist; a dataset item's source trace may have expired
        (GLOSSARY.md "Source trace expired")."""
        if not trace_ids:
            return LiveTraces(trace_ids=[])
        found = self.store.live(project, trace_ids)
        return LiveTraces(trace_ids=[t for t in trace_ids if t in found])

    def _read(self, project: UUID, trace_id: str) -> tuple[SummaryRow, list[SpanRow]]:
        summary = self.store.summary(project, trace_id)
        if summary is None:
            raise NotFound("trace not found; it may have expired")
        # The summary's time range bounds the scan on the spans sort key.
        return summary, self.store.spans(
            project, trace_id, summary.start_time, summary.last_end_time
        )

    def _text(self, span: SpanRow, which: Which) -> str:
        uri = span.input_uri if which == "input" else span.output_uri
        if not uri:
            return span.input_preview if which == "input" else span.output_preview
        return self.store.payload(uri)
