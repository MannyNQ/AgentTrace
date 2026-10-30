"""Reading traces: the trace list, one trace with its spans, a span's full payload, what a trace
did, and its steps for a fork (ADR-0031, ADR-0040). Trace completion and status are decided
here, when a trace is read (ADR-0013, GLOSSARY.md). Storage is behind `Store`; `trace_store`
has the ClickHouse queries."""

import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import UUID

from trayce.eval.results import ToolCall
from trayce.exporter import TRUNCATED_ATTRIBUTE
from trayce.tracing import (
    ERROR_MESSAGE_ATTRIBUTE,
    INTERCEPTOR_ATTRIBUTE,
    OUTPUT_RECORDED_ATTRIBUTE,
)
from trayce_ingest.datasets import DatasetError, NotFound, Record, Refused
from trayce_ingest.trace_inputs import decode_payload

if TYPE_CHECKING:
    from trayce_ingest.trace_filter import TraceFilter

SpanKind = Literal["llm", "tool", "agent", "retrieval", "custom"]
TraceStatus = Literal["ok", "error", "running"]
Which = Literal["input", "output"]
MAX_PAGE = 200
MAX_LIVE_IDS = 5000
MAX_STEPS = 1000  # every step's payloads are read, some from object storage
MAX_STEP_BYTES = 64 * 1024 * 1024  # and held in memory to answer, so they're bounded too


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
    service_name: str
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
    service_name: str  # the root span's resource.service.name, else ""


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
    """What a trace did, in the shape a replay run records (sdk `trayce.eval.runner`): its
    tool calls in start order, and the root span's output."""

    trajectory: list[ToolCall]
    output: Any
    root_error: str | None


class Step(Record):
    """One LLM or tool span with its full payloads, and what decides whether a fork can stub it
    (ADR-0040). `index` counts steps from 0 in start order, as a fork counts them."""

    index: int
    span_id: str
    parent_span_id: str
    kind: Literal["llm", "tool"]
    name: str
    start_time: datetime
    status: Literal["ok", "error"]
    error_type: str
    error_message: str | None
    interceptor: str | None  # the SDK hook that recorded it; None if none did
    truncated: list[Which]  # payloads the SDK cut to fit a request
    output_recorded: str | None  # on tool spans: `json`, `text`, or `lossy`
    input: Any
    output: Any


class Steps(Record):
    trace_id: str
    steps: list[Step]


class LiveTraces(Record):
    trace_ids: list[str]


class Store(Protocol):
    """Every method raises Unavailable when its store can't be read."""

    def summaries(
        self,
        project: UUID,
        f: "TraceFilter",
        complete_before: datetime,
        before: Cursor | None,
        limit: int,
    ) -> list[SummaryRow]:
        """The traces passing `f`, newest first, strictly before `before`. A trace last ingested
        before `complete_before` is complete even if its root never ended, which decides its
        status as `summarize` does."""
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
        service_name=r.service_name,
    )


def decode_step(text: str, recorded: str | None) -> Any:
    """A step's payload as the SDK recorded it. A bare string is stored as itself, so only the
    span's `text` mark tells it from JSON; without the mark, text that isn't JSON (a cut payload)
    stays text. An empty payload is None, unless the mark says it was the empty string."""
    if recorded == "text":
        return text
    if text == "":
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def _span(r: SpanRow) -> Span:
    shown = r.model_dump(exclude={"input_uri", "output_uri"})
    return Span(**shown, input_offloaded=bool(r.input_uri), output_offloaded=bool(r.output_uri))


class Traces:
    def __init__(self, store: Store, quiet_period: timedelta, now):
        self.store, self.quiet_period, self.now = store, quiet_period, now

    def page(self, project: UUID, f: "TraceFilter", before: Cursor | None, limit: int) -> TraceList:
        """The traces passing `f` (ADR-0038); by default, production traffic only."""
        limit = max(1, min(limit, MAX_PAGE))
        now = self.now()
        rows = self.store.summaries(project, f, now - self.quiet_period, before, limit + 1)
        page = rows[:limit]
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

    def steps(self, project: UUID, trace_id: str) -> Steps:
        """The trace's LLM and tool spans in start order, with full payloads, for a fork to play
        back. Whether each can be stubbed is the CLI's call, beside its interceptors."""
        _, spans = self._read(project, trace_id)
        rows = [s for s in spans if s.kind in ("llm", "tool")]
        if len(rows) > MAX_STEPS:
            raise Refused(f"the trace has {len(rows)} steps; a fork plays back at most {MAX_STEPS}")
        steps, total = [], 0
        for i, s in enumerate(rows):
            texts = self._text(s, "input"), self._text(s, "output")
            total += sum(len(t) for t in texts)
            if total > MAX_STEP_BYTES:
                raise Refused(
                    f"the trace's steps hold over {MAX_STEP_BYTES // 2**20} MB of payloads, more "
                    "than a fork plays back"
                )
            a = s.attributes
            recorded = a.get(OUTPUT_RECORDED_ATTRIBUTE)
            cut = a.get(TRUNCATED_ATTRIBUTE, "").split(",")
            steps.append(
                Step(
                    index=i,
                    span_id=s.span_id,
                    parent_span_id=s.parent_span_id,
                    kind="llm" if s.kind == "llm" else "tool",
                    name=s.name,
                    start_time=s.start_time,
                    status=s.status,
                    error_type=s.error_type,
                    error_message=a.get(ERROR_MESSAGE_ATTRIBUTE),
                    interceptor=a.get(INTERCEPTOR_ATTRIBUTE),
                    truncated=[w for w in ("input", "output") if w in cut],
                    output_recorded=recorded,
                    input=decode_step(texts[0], None),
                    output=decode_step(texts[1], recorded),
                )
            )
        return Steps(trace_id=trace_id, steps=steps)

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
