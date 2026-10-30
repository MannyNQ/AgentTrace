"""In-memory trace storage with the same contract as `trace_store.ClickHouseTraces`. The rules
under test are in `trayce_ingest.traces`."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from trayce_ingest.trace_filter import TraceFilter, matches
from trayce_ingest.traces import Cursor, SpanRow, SummaryRow, summarize

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def summary_row(trace_id: str, start: datetime = T0, **over: Any) -> SummaryRow:
    """A complete, ok, one-span trace unless `over` says otherwise."""
    end = start + timedelta(milliseconds=250)
    fields: dict[str, Any] = {
        "trace_id": trace_id,
        "start_time": start,
        "last_end_time": end,
        "last_ingested_at": end,
        "span_count": 1,
        "cost_usd": 0.0,
        "llm_spans": 0,
        "unpriced_llm_spans": 0,
        "has_root": True,
        "root_ended": True,
        "root_error": False,
        "root_end_time": end,
        "root_name": "agent.run",
        "session_id": "",
        "user_id": "",
        "tags": [],
        "replay_of": "",
        "service_name": "",
        "errors": 0,
    }
    return SummaryRow(**{**fields, **over})


def span_row(span_id: str, start: datetime = T0, **over: Any) -> SpanRow:
    fields: dict[str, Any] = {
        "span_id": span_id,
        "parent_span_id": "",
        "name": "agent.run",
        "kind": "agent",
        "start_time": start,
        "end_time": start + timedelta(milliseconds=10),
        "status": "ok",
        "error_type": "",
        "model": "",
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "cost_usd": None,
        "input_preview": "",
        "output_preview": "",
        "input_uri": "",
        "output_uri": "",
        "attributes": {},
    }
    return SpanRow(**{**fields, **over})


@dataclass
class FakeTraces:
    summaries_by: dict[tuple[UUID, str], SummaryRow] = field(default_factory=dict)
    spans_by: dict[tuple[UUID, str], list[SpanRow]] = field(default_factory=dict)
    objects: dict[str, str] = field(default_factory=dict)  # payload URI -> text
    reads: list[str] = field(default_factory=list)

    def add(self, project: UUID, summary: SummaryRow, spans: list[SpanRow] | None = None) -> None:
        self.summaries_by[(project, summary.trace_id)] = summary
        self.spans_by[(project, summary.trace_id)] = spans or []

    def summaries(
        self,
        project: UUID,
        f: TraceFilter,
        complete_before: datetime,
        before: Cursor | None,
        limit: int,
    ) -> list[SummaryRow]:
        # Read at `complete_before` with no quiet period, a trace is complete exactly when the
        # store's SQL says so.
        rows = [
            r
            for (p, t), r in self.summaries_by.items()
            if p == project
            and matches(f, summarize(r, complete_before, timedelta(0)), self.spans_by[(p, t)])
            and (
                before is None or (r.start_time, r.trace_id) < (before.start_time, before.trace_id)
            )
        ]
        rows.sort(key=lambda r: (r.start_time, r.trace_id), reverse=True)
        return rows[:limit]

    def summary(self, project: UUID, trace_id: str) -> SummaryRow | None:
        return self.summaries_by.get((project, trace_id))

    def spans(self, project: UUID, trace_id: str, start: datetime, end: datetime) -> list[SpanRow]:
        found = [
            s for s in self.spans_by.get((project, trace_id), []) if start <= s.start_time <= end
        ]
        return sorted(found, key=lambda s: (s.start_time, s.span_id))

    def live(self, project: UUID, trace_ids: list[str]) -> set[str]:
        return {t for t in trace_ids if (project, t) in self.summaries_by}

    def payload(self, uri: str) -> str:
        self.reads.append(uri)
        return self.objects[uri]
