"""/v1/traces: the trace list, one trace and its spans, a span's full payload, what a trace did,
which traces still exist, and a root span's input for `agenttrace replay` (ADR-0028, ADR-0031).
Each route parses, calls one rule, and returns its record."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import Field, StringConstraints

from agenttrace_ingest import traces as tr
from agenttrace_ingest.auth import Auth, ProjectAccess
from agenttrace_ingest.dataset_api import Body
from agenttrace_ingest.datasets import Refused, TraceId
from agenttrace_ingest.trace_filter import TraceFilter
from agenttrace_ingest.trace_inputs import RootInput, TraceRoots

PREFIX = "/v1/traces"

SpanId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")]


class ListQuery(TraceFilter):
    """The trace filter plus a page. One model, so an unknown parameter is refused rather than
    silently matching everything."""

    limit: int = 50
    before_start: datetime | None = None
    before_trace: TraceId | None = None

    @property
    def filter(self) -> TraceFilter:
        return TraceFilter(**self.model_dump(include=set(TraceFilter.model_fields)))


class LiveQuery(Body):
    trace_ids: Annotated[list[TraceId], Field(max_length=tr.MAX_LIVE_IDS)]


def router(traces: tr.Traces, roots: TraceRoots, a: Auth) -> APIRouter:
    r = APIRouter(prefix=PREFIX)
    auth = Depends(ProjectAccess(a))

    @r.get("")
    def list_traces(
        project: Annotated[UUID, auth], q: Annotated[ListQuery, Query()]
    ) -> tr.TraceList:
        """Newest first, filtered (ADR-0038). Pass the previous page's `next` as `before_start`
        and `before_trace`."""
        if (q.before_start is None) != (q.before_trace is None):
            # Ignoring half a cursor would return the first page again, and a client would loop.
            raise Refused("pass both before_start and before_trace, from the previous page's next")
        before = (
            tr.Cursor(start_time=q.before_start, trace_id=q.before_trace)
            if q.before_start is not None and q.before_trace is not None
            else None
        )
        return traces.page(project, q.filter, before, q.limit)

    @r.post("/live")
    def live(project: Annotated[UUID, auth], body: LiveQuery) -> tr.LiveTraces:
        """Which of these traces still exist. A POST, since a dataset can name thousands."""
        return traces.live(project, body.trace_ids)

    @r.get("/{trace_id}")
    def detail(project: Annotated[UUID, auth], trace_id: TraceId) -> tr.TraceDetail:
        return traces.detail(project, trace_id)

    @r.get("/{trace_id}/outcome")
    def outcome(project: Annotated[UUID, auth], trace_id: TraceId) -> tr.Outcome:
        """Its tool calls with full arguments, and the root span's output."""
        return traces.outcome(project, trace_id)

    @r.get("/{trace_id}/spans/{span_id}/{which}")
    def payload(
        project: Annotated[UUID, auth], trace_id: TraceId, span_id: SpanId, which: tr.Which
    ) -> tr.Payload:
        """A span's full input or output, from object storage when it was offloaded."""
        return traces.payload(project, trace_id, span_id, which)

    @r.get("/{trace_id}/input")
    def trace_input(project: Annotated[UUID, auth], trace_id: TraceId) -> RootInput:
        """The root span's full input, read as Add to dataset reads it; a truncated input is
        refused with 422."""
        return roots.root(project, trace_id)

    return r
