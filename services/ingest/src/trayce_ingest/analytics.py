"""The overview's numbers: volume, errors, latency, cost, tools, and models over 24h, 7d, or 30d
of a project's traces (architecture.md, Phase 8). The window and its buckets, the previous period,
running traces, and folding small models into "other" are decided here; `analytics_store` has
the ClickHouse queries. Every number is approximate (ADR-0037), and every query takes the trace
filter, so a chart agrees with the trace list it links to (ADR-0038)."""

import math
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, Protocol, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator, model_validator

from trayce_ingest.datasets import Record
from trayce_ingest.trace_filter import TraceFilter

Range = Literal["24h", "7d", "30d"]
Bucket = Literal["hour", "day"]
# Each range is this many buckets of this size.
RANGES: dict[Range, tuple[Bucket, int]] = {
    "24h": ("hour", 24),
    "7d": ("day", 7),
    "30d": ("day", 30),
}
MIN_CALLS = 20  # fewer calls than this and a tool's or model's p95 isn't ranked
TOP_SLOWEST = 10
TOP_MODELS = 5  # the spend chart's series; the rest are "other"
MAX_GROUPS = 500  # tools or models per response, by calls


class AnalyticsQuery(TraceFilter):
    """The trace filter, over a range instead of `since` and `until`, bucketed in `tz`."""

    range: Range = "24h"
    tz: str = "UTC"

    @field_validator("tz")
    @classmethod
    def _zone(cls, tz: str) -> str:
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("give an IANA time zone, such as Europe/Paris") from None
        return tz

    @model_validator(mode="after")
    def _range_not_times(self) -> Self:
        if self.since or self.until:
            raise ValueError("the range sets the times; leave out since and until")
        return self

    def over(self, since: datetime, until: datetime) -> TraceFilter:
        fields = self.model_dump(include=set(TraceFilter.model_fields))
        return TraceFilter(**{**fields, "since": since, "until": until})


class Window(Record):
    """What a response covers: [start, end), in buckets starting at `buckets` (UTC). The first
    bucket is partial when retention cuts it (`clamped`), and the last when it holds `end`."""

    range: Range
    bucket: Bucket
    tz: str
    start: datetime
    end: datetime
    buckets: list[datetime]
    retention_days: int
    clamped: bool


def window(r: Range, tz: str, now: datetime, retention_days: int) -> Window:
    """The range's buckets, ending with the one holding `now`, in `tz`'s hours or days; nothing
    before the project's retention, since older spans may already be gone."""
    bucket, count = RANGES[r]
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    if bucket == "hour":
        # Local hours start on UTC hour boundaries in every zone that matters here; stepping in UTC
        # gets a DST change's repeated or skipped hour right.
        last = local.replace(minute=0, second=0, microsecond=0).astimezone(UTC)
        starts = [last - timedelta(hours=k) for k in reversed(range(count))]
    else:
        today = local.date()
        starts = [_midnight(today - timedelta(days=k), zone) for k in reversed(range(count))]
    cutoff = now - timedelta(days=retention_days)
    ends = [*starts[1:], now]
    kept = [s for s, e in zip(starts, ends, strict=True) if e > cutoff]
    clamped = kept[0] < cutoff or len(kept) < len(starts)
    return Window(
        range=r,
        bucket=bucket,
        tz=tz,
        start=max(kept[0], cutoff),
        end=now,
        buckets=kept,
        retention_days=retention_days,
        clamped=clamped,
    )


def _midnight(d: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(d, time(), tzinfo=zone).astimezone(UTC)


def previous(w: Window) -> tuple[datetime, datetime] | None:
    """The period of the same length just before `w`, for the headline changes; None when any of
    it is past retention, since a partial period would make every change look like growth."""
    if w.clamped:
        return None
    start = w.start - (w.end - w.start)
    if start < w.end - timedelta(days=w.retention_days):
        return None
    return start, w.start


# Rows from the store. Times are bucket starts as Unix seconds; percentiles are milliseconds,
# None (or NaN) when nothing was measured.


class TraceRow(Record):
    """Trace-level aggregates over a bucket or a whole period."""

    bucket: int = 0  # 0 for a whole period
    traces: int
    running: int
    errors: int  # completed traces with status error
    p50: float | None
    p95: float | None
    p99: float | None
    cost: float  # of completed traces; unpriced calls count as 0 (ADR-0010)
    unpriced_llm_spans: int  # in completed traces


class KindRow(Record):
    kind: str
    spans: int
    errors: int


class GroupRow(Record):
    """Spans of one kind grouped by name (tools) or model (LLM calls)."""

    name: str
    calls: int
    errors: int
    p50: float | None
    p95: float | None


class ModelCostRow(Record):
    model: str
    calls: int
    unpriced_calls: int
    cost: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int  # 5-minute and 1-hour writes together


class ModelBucketRow(Record):
    bucket: int
    model: str
    cost: float


class Store(Protocol):
    """Each method reads the traces passing `f` (whose `since` and `until` are the period), with
    traces last ingested before `complete_before` complete, as the trace list decides. Span-level
    methods read those traces' spans. Every method raises traces.Unavailable when ClickHouse
    can't be read."""

    def trace_totals(
        self, project: UUID, f: TraceFilter, complete_before: datetime
    ) -> TraceRow: ...

    def trace_series(
        self, project: UUID, f: TraceFilter, complete_before: datetime, bucket: Bucket, tz: str
    ) -> tuple[TraceRow, list[TraceRow]]:
        """The totals over the period and each bucket's, in one pass."""
        ...

    def span_kinds(
        self, project: UUID, f: TraceFilter, complete_before: datetime
    ) -> list[KindRow]: ...

    def span_groups(
        self, project: UUID, f: TraceFilter, complete_before: datetime, kind: Literal["tool", "llm"]
    ) -> list[GroupRow]:
        """Tools by name or LLM calls by model, most calls first, at most MAX_GROUPS."""
        ...

    def model_costs(
        self, project: UUID, f: TraceFilter, complete_before: datetime, bucket: Bucket, tz: str
    ) -> tuple[list[ModelCostRow], list[ModelBucketRow]]:
        """Each model's calls, cost, and tokens, most calls first, at most MAX_GROUPS; and each
        model's spend per bucket. In one pass."""
        ...


# Responses.


class VolumeTotals(Record):
    traces: int
    running: int  # counted in traces; left out of error_rate until complete
    completed: int
    errors: int
    error_rate: float | None  # errors / completed


class VolumeBucket(VolumeTotals):
    start: datetime


class KindErrors(Record):
    kind: str
    spans: int
    errors: int
    error_rate: float | None


class Volume(Record):
    window: Window
    totals: VolumeTotals
    previous: VolumeTotals | None
    buckets: list[VolumeBucket]
    span_kinds: list[KindErrors]


class Percentiles(Record):
    completed: int
    p50: float | None
    p95: float | None
    p99: float | None


class LatencyBucket(Percentiles):
    start: datetime


class Latency(Record):
    window: Window
    totals: Percentiles
    previous: Percentiles | None
    buckets: list[LatencyBucket]


class CostTotals(Record):
    cost: float  # of completed traces
    completed: int
    cost_per_trace: float | None
    unpriced_llm_spans: int


class CostBucket(Record):
    start: datetime
    by_model: dict[str, float]  # the top models only
    other: float
    total: float


class ModelCost(Record):
    model: str
    calls: int
    unpriced_calls: int  # shown on their own, never priced at 0
    cost: float
    cost_per_call: float | None  # over priced calls
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


class Cost(Record):
    window: Window
    totals: CostTotals
    previous: CostTotals | None
    top_models: list[str]  # by spend in the window, at most TOP_MODELS
    buckets: list[CostBucket]
    models: list[ModelCost]


class Group(Record):
    name: str
    calls: int
    errors: int
    error_rate: float | None
    p50: float | None
    p95: float | None
    ranked: bool  # enough calls (MIN_CALLS) for its p95 to be ranked


class Groups(Record):
    window: Window
    min_calls: int
    groups: list[Group]  # most calls first
    slowest: list[str]  # names by p95, of ranked groups, at most TOP_SLOWEST


def _rate(part: float, whole: int) -> float | None:
    return part / whole if whole else None


def _other(total: float, shown: dict[str, float]) -> float:
    # Float subtraction can leave -1e-18 where there is nothing left over.
    return max(0.0, total - sum(shown.values()))


def _ms(value: float | None) -> float | None:
    return None if value is None or math.isnan(value) else value


def _volume(r: TraceRow) -> VolumeTotals:
    completed = r.traces - r.running
    return VolumeTotals(
        traces=r.traces,
        running=r.running,
        completed=completed,
        errors=r.errors,
        error_rate=_rate(r.errors, completed),
    )


def _percentiles(r: TraceRow) -> Percentiles:
    return Percentiles(
        completed=r.traces - r.running, p50=_ms(r.p50), p95=_ms(r.p95), p99=_ms(r.p99)
    )


def _cost(r: TraceRow) -> CostTotals:
    completed = r.traces - r.running
    return CostTotals(
        cost=r.cost,
        completed=completed,
        cost_per_trace=r.cost / completed if completed else None,
        unpriced_llm_spans=r.unpriced_llm_spans,
    )


def _empty(bucket: int) -> TraceRow:
    return TraceRow(
        bucket=bucket,
        traces=0,
        running=0,
        errors=0,
        p50=None,
        p95=None,
        p99=None,
        cost=0.0,
        unpriced_llm_spans=0,
    )


def _epoch(t: datetime) -> int:
    return int(t.timestamp())


class Analytics:
    def __init__(self, store: Store, quiet_period: timedelta, now):
        self.store, self.quiet_period, self.now = store, quiet_period, now

    def _window(self, q: AnalyticsQuery, retention_days: int) -> tuple[Window, datetime]:
        now = self.now()
        return window(q.range, q.tz, now, retention_days), now - self.quiet_period

    def _trace_series(
        self, project: UUID, q: AnalyticsQuery, w: Window, complete_before: datetime
    ) -> tuple[TraceRow, TraceRow | None, list[tuple[datetime, TraceRow]]]:
        f = q.over(w.start, w.end)
        totals, by_bucket = self.store.trace_series(project, f, complete_before, w.bucket, w.tz)
        prior = previous(w)
        before = (
            self.store.trace_totals(project, q.over(*prior), complete_before) if prior else None
        )
        rows = {r.bucket: r for r in by_bucket}
        series = [(b, rows.get(_epoch(b)) or _empty(_epoch(b))) for b in w.buckets]
        return totals, before, series

    def volume(self, project: UUID, q: AnalyticsQuery, retention_days: int) -> Volume:
        w, complete_before = self._window(q, retention_days)
        totals, before, series = self._trace_series(project, q, w, complete_before)
        kinds = self.store.span_kinds(project, q.over(w.start, w.end), complete_before)
        return Volume(
            window=w,
            totals=_volume(totals),
            previous=_volume(before) if before else None,
            buckets=[VolumeBucket(start=b, **_volume(r).model_dump()) for b, r in series],
            span_kinds=[
                KindErrors(
                    kind=k.kind, spans=k.spans, errors=k.errors, error_rate=_rate(k.errors, k.spans)
                )
                for k in kinds
            ],
        )

    def latency(self, project: UUID, q: AnalyticsQuery, retention_days: int) -> Latency:
        w, complete_before = self._window(q, retention_days)
        totals, before, series = self._trace_series(project, q, w, complete_before)
        return Latency(
            window=w,
            totals=_percentiles(totals),
            previous=_percentiles(before) if before else None,
            buckets=[LatencyBucket(start=b, **_percentiles(r).model_dump()) for b, r in series],
        )

    def cost(self, project: UUID, q: AnalyticsQuery, retention_days: int) -> Cost:
        w, complete_before = self._window(q, retention_days)
        f = q.over(w.start, w.end)
        totals = self.store.trace_totals(project, f, complete_before)
        prior = previous(w)
        before = (
            self.store.trace_totals(project, q.over(*prior), complete_before) if prior else None
        )
        table, by_bucket = self.store.model_costs(project, f, complete_before, w.bucket, w.tz)
        models = sorted(table, key=lambda m: (-m.cost, m.model))
        top = [m.model for m in models if m.cost > 0][:TOP_MODELS]
        spend: dict[int, dict[str, float]] = {}
        for r in by_bucket:
            spend.setdefault(r.bucket, {})[r.model] = r.cost
        buckets = []
        for b in w.buckets:
            costs = spend.get(_epoch(b), {})
            shown = {m: costs.get(m, 0.0) for m in top}
            total = sum(costs.values())
            buckets.append(
                CostBucket(start=b, by_model=shown, other=_other(total, shown), total=total)
            )
        return Cost(
            window=w,
            totals=_cost(totals),
            previous=_cost(before) if before else None,
            top_models=top,
            buckets=buckets,
            models=[
                ModelCost(
                    **m.model_dump(),
                    cost_per_call=_rate(m.cost, m.calls - m.unpriced_calls),
                )
                for m in models
            ],
        )

    def groups(
        self, project: UUID, q: AnalyticsQuery, retention_days: int, kind: Literal["tool", "llm"]
    ) -> Groups:
        """Tools by name, or LLM calls by model."""
        w, complete_before = self._window(q, retention_days)
        rows = self.store.span_groups(project, q.over(w.start, w.end), complete_before, kind)
        groups = [
            Group(
                name=r.name,
                calls=r.calls,
                errors=r.errors,
                error_rate=_rate(r.errors, r.calls),
                p50=_ms(r.p50),
                p95=_ms(r.p95),
                ranked=r.calls >= MIN_CALLS and _ms(r.p95) is not None,
            )
            for r in rows
        ]
        ranked = sorted((g for g in groups if g.ranked), key=lambda g: (-(g.p95 or 0), g.name))
        return Groups(
            window=w,
            min_calls=MIN_CALLS,
            groups=groups,
            slowest=[g.name for g in ranked[:TOP_SLOWEST]],
        )
