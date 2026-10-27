"""A trace's root span input, read server-side for "Add to dataset" and for `agenttrace replay`
(GLOSSARY.md, ADR-0028)."""

import json
from collections.abc import Callable
from functools import cached_property
from typing import Any, Protocol
from uuid import UUID

from agenttrace.eval.results import SpanRef
from agenttrace_ingest.datasets import NotFound, Record, Refused
from agenttrace_ingest.settings import Settings
from agenttrace_ingest.stores import clickhouse_client, read_payload, s3_client

TRUNCATED_ATTRIBUTE = "agenttrace.truncated"  # sdk/python/src/agenttrace/exporter.py
REPLAY_OF_ATTRIBUTE = "agenttrace.replay_of"  # sdk/python/src/agenttrace/eval/replay.py


class RootInput(Record):
    """A trace's root span and its full input. `replay_of` is set when the trace is itself a
    replay run, naming the root span of the original it re-ran: for a fork of a fork, the one at
    the start of the chain (ADR-0040)."""

    trace_id: str
    span_id: str
    input: Any
    replay_of: SpanRef | None


class TraceRoots(Protocol):
    def root(self, project: UUID, trace_id: str) -> RootInput:
        """Raises NotFound or Refused, as `root_input` does."""
        ...

    def exists(self, project: UUID, trace_id: str) -> bool:
        """Whether the project holds this trace, without reading its spans or payloads."""
        ...

    def origin(self, project: UUID, trace_id: str) -> SpanRef:
        """The root span of the original this trace re-ran, or its own root span when it isn't
        a replay run, as `resolve_origin` finds it. Raises NotFound."""
        ...


MAX_CHAIN = 64
# A trace's root span ID and its `replay_of`, or None when the trace is gone.
RootLookup = Callable[[str], tuple[str, SpanRef | None] | None]


def resolve_origin(ref: SpanRef, lookup: RootLookup) -> SpanRef:
    """Follows `replay_of` from `ref` to the trace that isn't a replay run, and returns its root
    span. A whole-trace replay points at its original's root already; a fork points at a step in
    the trace it forked, which may itself be a replay run (ADR-0040). A trace in the chain that
    has expired ends the walk at the last reference known; a cycle, which only hand-made spans
    could form, ends it too."""
    seen: set[str] = set()
    while ref.trace_id not in seen and len(seen) < MAX_CHAIN:
        seen.add(ref.trace_id)
        found = lookup(ref.trace_id)
        if found is None:
            return ref
        root_span, parent = found
        if parent is None:
            return SpanRef(trace_id=ref.trace_id, span_id=root_span)
        ref = parent
    return ref


def parse_ref(value: str) -> SpanRef | None:
    """`<trace_id>:<span_id>` as the runner writes it, or None when unset or malformed."""
    trace_id, _, span_id = value.partition(":")
    try:
        return SpanRef(trace_id=trace_id, span_id=span_id)
    except ValueError:
        return None


def decode_payload(text: str) -> Any:
    """The worker stores a string payload as the bare string and anything else as JSON, so a
    stored `123` may have been the number or the string. Only an object or array is taken as
    JSON: root inputs are argument objects, and a string that merely looks like a scalar stays a
    string. A non-string scalar root input therefore comes back as its text."""
    try:
        value = json.loads(text)
    except ValueError:
        return text
    return value if isinstance(value, dict | list) else text


class StoredTraceInputs:
    def __init__(self, s: Settings):
        self.s = s

    # Lazy: both clients connect when built, and the app builds this object at import time.
    @cached_property
    def _clickhouse(self):
        return clickhouse_client(self.s)

    @cached_property
    def _s3(self):
        return s3_client(self.s)

    def root_input(self, project: UUID, trace_id: str) -> Any:
        return self.root(project, trace_id).input

    def exists(self, project: UUID, trace_id: str) -> bool:
        return bool(
            self._clickhouse.query(
                """SELECT 1 FROM traces
                   WHERE project_id = {project:UUID} AND trace_id = {trace:FixedString(32)}
                   LIMIT 1""",
                parameters={"project": str(project), "trace": trace_id},
            ).result_rows
        )

    def origin(self, project: UUID, trace_id: str) -> SpanRef:
        row = self._root_row(project, trace_id)
        if row is None:
            raise NotFound("trace not found; it may have expired")
        span_id, *_, replay_of = row
        parent = parse_ref(replay_of)
        if parent is None:
            return SpanRef(trace_id=trace_id, span_id=span_id)
        return resolve_origin(parent, self._lookup_in(project))

    def _lookup_in(self, project: UUID) -> RootLookup:
        def lookup(trace_id: str) -> tuple[str, SpanRef | None] | None:
            row = self._root_row(project, trace_id)
            return None if row is None else (row[0], parse_ref(row[-1]))

        return lookup

    def _root_row(self, project: UUID, trace_id: str) -> tuple | None:
        """(span_id, input_preview, input_uri, truncated, replay_of) of the root span; None when
        the trace or its root span isn't stored."""
        ch = self._clickhouse
        params = {"project": str(project), "trace": trace_id}
        # The summary's time range bounds the scan on the spans sort key (ADR-0013).
        summary = ch.query(
            """SELECT min(start_time), max(last_end_time), max(has_root) FROM traces
               WHERE project_id = {project:UUID} AND trace_id = {trace:FixedString(32)}
               GROUP BY trace_id""",
            parameters=params,
        ).result_rows
        if not summary or not summary[0][2]:
            return None
        start, end, _ = summary[0]
        rows = ch.query(
            """SELECT toString(span_id), input_preview, input_uri, attributes[{truncated:String}],
                      attributes[{replay_of:String}]
               FROM spans FINAL
               WHERE project_id = {project:UUID} AND trace_id = {trace:FixedString(32)}
                 AND parent_span_id = '' AND start_time BETWEEN {start:DateTime64(6)}
                 AND {end:DateTime64(6)}
               LIMIT 1""",
            parameters={
                **params,
                "start": start,
                "end": end,
                "truncated": TRUNCATED_ATTRIBUTE,
                "replay_of": REPLAY_OF_ATTRIBUTE,
            },
        ).result_rows
        return tuple(rows[0]) if rows else None

    def root(self, project: UUID, trace_id: str) -> RootInput:
        row = self._root_row(project, trace_id)
        if row is None:
            if self.exists(project, trace_id):
                raise Refused("the trace's root span hasn't arrived, so there's no input to copy")
            raise NotFound("trace not found; it may have expired")
        span_id, preview, uri, truncated, replay_of = row
        if "input" in truncated.split(","):
            raise Refused("the SDK cut this trace's input to fit a request, so it can't be copied")
        text = read_payload(self.s, self._s3, uri) if uri else preview
        if text == "":
            raise Refused("the trace's root span has no input")
        parent = parse_ref(replay_of)
        return RootInput(
            trace_id=trace_id,
            span_id=span_id,
            input=decode_payload(text),
            replay_of=None if parent is None else resolve_origin(parent, self._lookup_in(project)),
        )
