"""A trace's root span input, read server-side for "Add to dataset" and for `agenttrace replay`
(GLOSSARY.md, ADR-0028)."""

import json
from functools import cached_property
from typing import Any, Protocol
from uuid import UUID

from agenttrace.eval.results import SpanRef
from agenttrace_ingest.datasets import NotFound, Record, Refused
from agenttrace_ingest.settings import Settings
from agenttrace_ingest.stores import clickhouse_client, s3_client

TRUNCATED_ATTRIBUTE = "agenttrace.truncated"  # sdk/python/src/agenttrace/exporter.py
REPLAY_OF_ATTRIBUTE = "agenttrace.replay_of"  # sdk/python/src/agenttrace/eval/replay.py


class RootInput(Record):
    """A trace's root span and its full input. `replay_of` is set when the trace is itself a
    replay run, naming the original it re-ran."""

    trace_id: str
    span_id: str
    input: Any
    replay_of: SpanRef | None


class TraceRoots(Protocol):
    def root(self, project: UUID, trace_id: str) -> RootInput:
        """Raises NotFound or Refused, as `root_input` does."""
        ...


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

    def root(self, project: UUID, trace_id: str) -> RootInput:
        ch = self._clickhouse
        params = {"project": str(project), "trace": trace_id}
        # The summary's time range bounds the scan on the spans sort key (ADR-0013).
        summary = ch.query(
            """SELECT min(start_time), max(last_end_time), max(has_root) FROM traces
               WHERE project_id = {project:UUID} AND trace_id = {trace:FixedString(32)}
               GROUP BY trace_id""",
            parameters=params,
        ).result_rows
        if not summary:
            raise NotFound("trace not found; it may have expired")
        start, end, has_root = summary[0]
        if not has_root:
            raise Refused("the trace's root span hasn't arrived, so there's no input to copy")
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
        if not rows:
            raise NotFound("trace not found; it may have expired")
        span_id, preview, uri, truncated, replay_of = rows[0]
        if "input" in truncated.split(","):
            raise Refused("the SDK cut this trace's input to fit a request, so it can't be copied")
        text = self._read(uri) if uri else preview
        if text == "":
            raise Refused("the trace's root span has no input")
        return RootInput(
            trace_id=trace_id,
            span_id=span_id,
            input=decode_payload(text),
            replay_of=parse_ref(replay_of),
        )

    def _read(self, uri: str) -> str:
        prefix = f"s3://{self.s.s3_bucket}/"
        if not uri.startswith(prefix):
            raise RuntimeError(f"unexpected payload uri: {uri}")
        body = self._s3.get_object(Bucket=self.s.s3_bucket, Key=uri[len(prefix) :])["Body"]
        return body.read().decode()
