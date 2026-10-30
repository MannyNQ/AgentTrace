"""ClickHouse queries for the overview (ADR-0037): aggregates over the traces passing the trace
filter and their spans, without FINAL and with t-digest percentiles. Rows only; the window,
buckets, and responses are decided in `analytics`."""

from collections.abc import Sequence
from datetime import datetime
from functools import cached_property
from typing import Any, Literal
from uuid import UUID

from clickhouse_connect.driver.exceptions import ClickHouseError

from trayce_ingest.analytics import (
    MAX_GROUPS,
    Bucket,
    GroupRow,
    KindRow,
    ModelBucketRow,
    ModelCostRow,
    TraceRow,
)
from trayce_ingest.errors import Refused
from trayce_ingest.settings import Settings
from trayce_ingest.stores import clickhouse_client
from trayce_ingest.trace_filter import TraceFilter
from trayce_ingest.trace_store import COMPLETE, clickhouse_reads, filtered_traces_sql

UNKNOWN_ZONE = "Cannot load time zone"  # ClickHouse's error for a zone it doesn't have

# Fixed per bucket size, never from the request; the zone is a bound parameter.
BUCKET_OF = {"hour": "toStartOfHour", "day": "toStartOfDay"}

LATENCY_MS = "dateDiff('microsecond', t_start, if(t_root_ended = 1, t_root_end, t_last_end)) / 1000"
# Traces, running, errors, latency percentiles, cost, unpriced LLM spans. No aliases: ClickHouse
# resolves an alias before a column anywhere in the query (eng-log, Phase 1), and `traces` is a
# table here.
TRACE_AGGREGATES = f"""
    count(),
    countIf(NOT {COMPLETE}),
    countIf({COMPLETE} AND (t_root_error = 1 OR t_root_ended = 0)),
    quantilesTDigestIf(0.5, 0.95, 0.99)({LATENCY_MS}, {COMPLETE}),
    sumIf(t_cost, {COMPLETE}),
    sumIf(t_unpriced, {COMPLETE})"""

SPAN_MS = "dateDiff('microsecond', start_time, end_time) / 1000"


def _bucket(bucket: Bucket, column: str) -> str:
    return f"toUnixTimestamp({BUCKET_OF[bucket]}({column}, {{tz:String}}))"


# What an aggregate over no traces returns, for a period with none.
EMPTY_AGGREGATES = (0, 0, 0, [float("nan")] * 3, 0.0, 0)


def _trace_row(row: Sequence[Any], bucket: int = 0) -> TraceRow:
    traces, running, errors, q, cost, unpriced = row
    return TraceRow(
        bucket=bucket,
        traces=traces,
        running=running,
        errors=errors,
        p50=q[0],
        p95=q[1],
        p99=q[2],
        cost=cost,
        unpriced_llm_spans=unpriced,
    )


class ClickHouseAnalytics:
    def __init__(self, s: Settings):
        self.s = s

    @cached_property
    def _clickhouse(self):
        return clickhouse_client(self.s)

    def _query(self, sql: str, parameters: dict) -> Sequence[Sequence[Any]]:
        with clickhouse_reads():
            try:
                return self._clickhouse.query(sql, parameters=parameters).result_rows
            except ClickHouseError as e:
                # Python's zone database and ClickHouse's can differ; the caller's zone is at
                # fault, not the store.
                if "tz" in parameters and UNKNOWN_ZONE in str(e):
                    raise Refused(
                        f"ClickHouse doesn't know the time zone {parameters['tz']}"
                    ) from None
                raise

    def _traces(self, project: UUID, f: TraceFilter, complete_before: datetime):
        sql, params = filtered_traces_sql(f, complete_before)
        return sql, {**params, "project": str(project)}

    def _spans(
        self, project: UUID, f: TraceFilter, complete_before: datetime, where: str
    ) -> tuple[str, dict[str, Any]]:
        """FROM and WHERE over the spans of the traces passing `f`. A trace's spans start no
        earlier than the trace, so `since` bounds the scan on the spans sort key."""
        traces, params = self._traces(project, f, complete_before)
        sql = f"""FROM spans
            WHERE project_id = {{project:UUID}}
              AND start_time >= {{a_since:DateTime64(6)}}
              AND trace_id IN (SELECT t_key FROM ({traces}))
              {where}"""
        return sql, {**params, "a_since": f.since}

    def trace_totals(self, project: UUID, f: TraceFilter, complete_before: datetime) -> TraceRow:
        traces, params = self._traces(project, f, complete_before)
        (row,) = self._query(f"SELECT {TRACE_AGGREGATES} FROM ({traces})", params)
        return _trace_row(row)

    def trace_series(
        self, project: UUID, f: TraceFilter, complete_before: datetime, bucket: Bucket, tz: str
    ) -> tuple[TraceRow, list[TraceRow]]:
        # One pass for the totals and the buckets: grouping the traces is most of the cost. The
        # totals' row is the one with no bucket (0; no bucket starts at the epoch).
        traces, params = self._traces(project, f, complete_before)
        rows = self._query(
            f"""SELECT {_bucket(bucket, "t_start")} AS b, {TRACE_AGGREGATES}
                FROM ({traces}) GROUP BY GROUPING SETS ((b), ()) ORDER BY b""",
            {**params, "tz": tz},
        )
        totals = next((_trace_row(r[1:]) for r in rows if r[0] == 0), None)
        return totals or _trace_row(EMPTY_AGGREGATES), [
            _trace_row(r[1:], r[0]) for r in rows if r[0] != 0
        ]

    def span_kinds(self, project: UUID, f: TraceFilter, complete_before: datetime) -> list[KindRow]:
        spans, params = self._spans(project, f, complete_before, "")
        rows = self._query(
            f"""SELECT toString(kind) AS k, count(), countIf(status = 'error')
                {spans} GROUP BY k ORDER BY k""",
            params,
        )
        return [KindRow(kind=k, spans=n, errors=e) for k, n, e in rows]

    def span_groups(
        self, project: UUID, f: TraceFilter, complete_before: datetime, kind: Literal["tool", "llm"]
    ) -> list[GroupRow]:
        key = {"tool": "name", "llm": "model"}[kind]
        spans, params = self._spans(project, f, complete_before, "AND kind = {a_kind:String}")
        rows = self._query(
            f"""SELECT {key} AS g, count() AS calls, countIf(status = 'error'),
                       quantilesTDigestIf(0.5, 0.95)({SPAN_MS}, end_time IS NOT NULL)
                {spans} GROUP BY g ORDER BY calls DESC, g LIMIT {MAX_GROUPS}""",
            {**params, "a_kind": kind},
        )
        return [
            GroupRow(name=g, calls=calls, errors=errors, p50=q[0], p95=q[1])
            for g, calls, errors, q in rows
        ]

    def model_costs(
        self, project: UUID, f: TraceFilter, complete_before: datetime, bucket: Bucket, tz: str
    ) -> tuple[list[ModelCostRow], list[ModelBucketRow]]:
        # One pass for the per-model table (no bucket: 0) and each model's spend per bucket.
        spans, params = self._spans(project, f, complete_before, "AND kind = 'llm'")
        rows = self._query(
            f"""SELECT {_bucket(bucket, "start_time")} AS b, model AS m, count(),
                       countIf(cost_usd IS NULL), sum(ifNull(cost_usd, 0)),
                       sum(ifNull(input_tokens, 0)), sum(ifNull(output_tokens, 0)),
                       sum(ifNull(cache_read_tokens, 0)),
                       sum(ifNull(cache_write_5m_tokens, 0) + ifNull(cache_write_1h_tokens, 0))
                {spans} GROUP BY GROUPING SETS ((m), (b, m)) ORDER BY b, m""",
            {**params, "tz": tz},
        )
        table = sorted(
            (
                ModelCostRow(
                    model=m,
                    calls=calls,
                    unpriced_calls=unpriced,
                    cost=cost,
                    input_tokens=i,
                    output_tokens=o,
                    cache_read_tokens=cr,
                    cache_write_tokens=cw,
                )
                for b, m, calls, unpriced, cost, i, o, cr, cw in rows
                if b == 0
            ),
            key=lambda r: (-r.calls, r.model),
        )[:MAX_GROUPS]
        buckets = [ModelBucketRow(bucket=r[0], model=r[1], cost=r[4]) for r in rows if r[0] != 0]
        return table, buckets
