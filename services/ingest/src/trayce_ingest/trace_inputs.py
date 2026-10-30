"""A trace's root span input, read server-side for "Add to dataset" and for `trayce replay`
(GLOSSARY.md, ADR-0028)."""

import json
from functools import cached_property
from typing import Any, Protocol
from uuid import UUID

from trayce.eval.results import SpanRef
from trayce_ingest.datasets import NotFound, Record, Refused
from trayce_ingest.settings import Settings
from trayce_ingest.stores import clickhouse_client, read_payload, s3_client

TRUNCATED_ATTRIBUTE = "trayce.truncated"  # sdk/python/src/trayce/exporter.py
REPLAY_OF_ATTRIBUTE = "trayce.replay_of"  # sdk/python/src/trayce/eval/replay.py
REPLAY_ORIGIN_ATTRIBUTE = "trayce.replay_origin"  # the same file


class RootInput(Record):
    """A trace's root span and its full input. `replay_of` is set when the trace is itself a
    replay run, naming the root span of the original it re-ran: for a fork, even of a fork, the
    one at the start of the chain (ADR-0040)."""

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
        a replay run, as `origin_of` decides. Raises NotFound."""
        ...


def origin_of(replay_of: SpanRef | None, replay_origin: SpanRef | None) -> SpanRef | None:
    """The original a replay run re-ran, from its root span's attributes, or None for a trace
    that isn't one. A fork run names it in `replay_origin`, since its `replay_of` is the fork
    point; a whole-trace replay's `replay_of` is the original's root already. Neither needs the
    traces between them, which may have expired (ADR-0040)."""
    return replay_origin or replay_of


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
        span_id, *_, replay_of, replay_origin = row
        found = origin_of(parse_ref(replay_of), parse_ref(replay_origin))
        return found or SpanRef(trace_id=trace_id, span_id=span_id)

    def _root_row(self, project: UUID, trace_id: str) -> tuple | None:
        """(span_id, input_preview, input_uri, truncated, replay_of, replay_origin) of the root
        span; None when the trace or its root span isn't stored."""
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
                      attributes[{replay_of:String}], attributes[{replay_origin:String}]
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
                "replay_origin": REPLAY_ORIGIN_ATTRIBUTE,
            },
        ).result_rows
        return tuple(rows[0]) if rows else None

    def root(self, project: UUID, trace_id: str) -> RootInput:
        row = self._root_row(project, trace_id)
        if row is None:
            if self.exists(project, trace_id):
                raise Refused("the trace's root span hasn't arrived, so there's no input to copy")
            raise NotFound("trace not found; it may have expired")
        span_id, preview, uri, truncated, replay_of, replay_origin = row
        if "input" in truncated.split(","):
            raise Refused("the SDK cut this trace's input to fit a request, so it can't be copied")
        text = read_payload(self.s, self._s3, uri) if uri else preview
        if text == "":
            raise Refused("the trace's root span has no input")
        return RootInput(
            trace_id=trace_id,
            span_id=span_id,
            input=decode_payload(text),
            replay_of=origin_of(parse_ref(replay_of), parse_ref(replay_origin)),
        )
