"""ClickHouse and S3 storage for trace reads (schema: infra/clickhouse, migrations/clickhouse).
Rows only; completion, status, and what a trace did are decided in `traces`."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import cached_property
from typing import Any
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from clickhouse_connect.driver.exceptions import ClickHouseError

from agenttrace_ingest.settings import Settings
from agenttrace_ingest.stores import clickhouse_client, read_payload, s3_client
from agenttrace_ingest.traces import Cursor, SpanRow, SummaryRow, Unavailable

# traces is an AggregatingMergeTree (ADR-0013): merge unmerged parts with the same aggregates the
# materialized view used. Aliases differ from column names so HAVING can't confuse them.
SUMMARY_SELECT = """
    SELECT
        toString(trace_id) AS t_trace_id,
        min(start_time) AS t_start,
        max(last_end_time) AS t_last_end,
        max(last_ingested_at) AS t_last_ingested,
        sum(span_count) AS t_span_count,
        sum(cost_usd) AS t_cost,
        sum(llm_spans) AS t_llm_spans,
        sum(unpriced_llm_spans) AS t_unpriced,
        max(has_root) AS t_has_root,
        max(root_ended) AS t_root_ended,
        max(root_error) AS t_root_error,
        max(root_end_time) AS t_root_end,
        max(root_name) AS t_root_name,
        max(session_id) AS t_session_id,
        max(user_id) AS t_user_id,
        groupUniqArrayArray(tags) AS t_tags,
        max(replay_of) AS t_replay_of,
        sum(llm_errors) + sum(tool_errors) + sum(agent_errors) + sum(retrieval_errors)
          + sum(custom_errors) AS t_errors
    FROM traces
    WHERE project_id = {project:UUID}"""

SPAN_SELECT = """
    SELECT toString(span_id), parent_span_id, name, toString(kind), start_time, end_time,
           toString(status), error_type, model, input_tokens, output_tokens, cache_read_tokens,
           if(cache_write_5m_tokens IS NULL AND cache_write_1h_tokens IS NULL, NULL,
              ifNull(cache_write_5m_tokens, 0) + ifNull(cache_write_1h_tokens, 0)),
           cost_usd, input_preview, output_preview, input_uri, output_uri, attributes
    FROM spans FINAL
    WHERE project_id = {project:UUID}
      AND trace_id = {trace:FixedString(32)}
      AND start_time BETWEEN {start:DateTime64(6)} AND {end:DateTime64(6)}
    ORDER BY start_time, span_id"""

SPAN_FIELDS = list(SpanRow.model_fields)

# No cursor: start after every trace. ClickHouse parses constants even in a dead OR branch, so an
# empty cursor can't be special-cased in SQL.
AFTER_EVERYTHING = Cursor(start_time=datetime(2999, 1, 1, tzinfo=UTC), trace_id="f" * 32)


# Columns ClickHouse migrations add (migrations/clickhouse). Reading one before its migration has
# run is an outage with a known cause, not a bug.
MIGRATED_COLUMNS = ("replay_of",)
MAX_REASON_CHARS = 300


def _reason(e: Exception) -> str:
    text = str(e).strip().splitlines()[0] if str(e).strip() else ""
    return f"{type(e).__name__}: {text}"[:MAX_REASON_CHARS]


def clickhouse_problem(e: Exception) -> str:
    missing = next((c for c in MIGRATED_COLUMNS if c in str(e)), None)
    if missing:
        return (
            f"ClickHouse doesn't have the `{missing}` column yet. Ingest applies its ClickHouse "
            "migrations in the background as soon as ClickHouse answers; its logs say why they "
            "haven't run."
        )
    return f"ClickHouse can't be read ({_reason(e)})"


@contextmanager
def clickhouse_reads() -> Iterator[None]:
    try:
        yield
    except (ClickHouseError, OSError) as e:
        raise Unavailable(clickhouse_problem(e)) from e


@contextmanager
def object_storage_reads() -> Iterator[None]:
    try:
        yield
    except (BotoCoreError, ClientError, OSError) as e:
        raise Unavailable(f"object storage can't be read ({_reason(e)})") from e


def utc(value: datetime) -> datetime:
    """clickhouse_connect returns a `DateTime64(6, 'UTC')` naive when the process's local zone is
    UTC (as in the container and CI) and aware otherwise, so every datetime is made aware here."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _summary(row: Sequence[Any]) -> SummaryRow:
    (trace_id, start, last_end, last_ingested, spans, cost, llm, unpriced, has_root, ended, error,
     root_end, root_name, session, user, tags, replay_of, errors) = row  # fmt: skip
    return SummaryRow(
        trace_id=trace_id,
        start_time=utc(start),
        last_end_time=utc(last_end),
        last_ingested_at=utc(last_ingested),
        span_count=spans,
        cost_usd=cost,
        llm_spans=llm,
        unpriced_llm_spans=unpriced,
        has_root=bool(has_root),
        root_ended=bool(ended),
        root_error=bool(error),
        root_end_time=utc(root_end),
        root_name=root_name,
        session_id=session,
        user_id=user,
        tags=tags,
        replay_of=replay_of,
        errors=errors,
    )


def _span(row: Sequence[Any]) -> SpanRow:
    fields = dict(zip(SPAN_FIELDS, row, strict=True))
    fields["start_time"] = utc(fields["start_time"])
    if fields["end_time"] is not None:
        fields["end_time"] = utc(fields["end_time"])
    return SpanRow(**fields)


class ClickHouseTraces:
    def __init__(self, s: Settings):
        self.s = s

    # Lazy: both clients connect when built, and the app builds this object at import time.
    @cached_property
    def _clickhouse(self):
        return clickhouse_client(self.s)

    @cached_property
    def _s3(self):
        return s3_client(self.s)

    def _query(self, sql: str, parameters: dict) -> Sequence[Sequence[Any]]:
        with clickhouse_reads():  # building the client connects, so it's inside too
            return self._clickhouse.query(sql, parameters=parameters).result_rows

    def summaries(
        self, project: UUID, before: Cursor | None, show_replays: bool, limit: int
    ) -> list[SummaryRow]:
        before = before or AFTER_EVERYTHING
        rows = self._query(
            f"""{SUMMARY_SELECT}
                GROUP BY trace_id
                HAVING (t_start, t_trace_id)
                       < ({{before_start:DateTime64(6)}}, {{before_trace:String}})
                   AND ({{show_replays:UInt8}} = 1 OR t_replay_of = '')
                ORDER BY t_start DESC, t_trace_id DESC
                LIMIT {{limit:UInt32}}""",
            {
                "project": str(project),
                "before_start": before.start_time,
                "before_trace": before.trace_id,
                "show_replays": int(show_replays),
                "limit": limit,
            },
        )
        return [_summary(r) for r in rows]

    def summary(self, project: UUID, trace_id: str) -> SummaryRow | None:
        rows = self._query(
            f"{SUMMARY_SELECT} AND trace_id = {{trace:FixedString(32)}} GROUP BY trace_id",
            {"project": str(project), "trace": trace_id},
        )
        return _summary(rows[0]) if rows else None

    def spans(self, project: UUID, trace_id: str, start: datetime, end: datetime) -> list[SpanRow]:
        # FINAL hides duplicates that ReplacingMergeTree hasn't merged yet (ADR-0014).
        rows = self._query(
            SPAN_SELECT,
            {"project": str(project), "trace": trace_id, "start": start, "end": end},
        )
        return [_span(r) for r in rows]

    def live(self, project: UUID, trace_ids: list[str]) -> set[str]:
        rows = self._query(
            """SELECT DISTINCT toString(trace_id) FROM traces
               WHERE project_id = {project:UUID} AND trace_id IN {ids:Array(FixedString(32))}""",
            {"project": str(project), "ids": trace_ids},
        )
        return {r[0] for r in rows}

    def payload(self, uri: str) -> str:
        with object_storage_reads():
            return read_payload(self.s, self._s3, uri)
