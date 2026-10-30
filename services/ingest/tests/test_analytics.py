"""The overview's rules with in-memory storage: the window and its buckets across time zones and
DST, retention, the previous period, running traces, top models and "other", and ranking the
slowest; and /v1/analytics's parameters and access (ADR-0037, ADR-0038)."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fake_auth import FakeAuth
from fake_datasets import FakeDatasets, FakePins
from fastapi.testclient import TestClient

from trayce_ingest import analytics as an
from trayce_ingest.app import create_app
from trayce_ingest.settings import Settings
from trayce_ingest.trace_filter import TraceFilter

PROJECT = UUID("00000000-0000-0000-0000-0000000000aa")
NOW = datetime(2026, 10, 26, 15, 40, tzinfo=UTC)


def at(epoch: datetime) -> int:
    return int(epoch.timestamp())


def test_24h_is_24_hourly_buckets_ending_with_the_current_hour():
    w = an.window("24h", "UTC", NOW, 30)
    assert len(w.buckets) == 24 and w.bucket == "hour"
    assert w.buckets[-1] == datetime(2026, 10, 26, 15, tzinfo=UTC)
    assert w.buckets[0] == datetime(2026, 10, 25, 16, tzinfo=UTC) == w.start
    assert w.end == NOW and not w.clamped


def test_hours_follow_a_half_hour_zone():
    w = an.window("24h", "Asia/Kolkata", NOW, 30)  # UTC+5:30: 21:10 local
    assert w.buckets[-1] == datetime(2026, 10, 26, 15, 30, tzinfo=UTC)


def test_days_are_local_midnights_across_a_dst_change():
    # Paris leaves summer time on 2026-10-25: that day is 25 hours long.
    w = an.window("7d", "Europe/Paris", NOW, 30)
    assert len(w.buckets) == 7
    assert w.buckets[-1] == datetime(2026, 10, 25, 23, tzinfo=UTC)  # midnight CET
    assert w.buckets[-2] == datetime(2026, 10, 24, 22, tzinfo=UTC)  # midnight CEST
    assert w.buckets[-1] - w.buckets[-2] == timedelta(hours=25)


def test_the_window_stops_at_retention():
    w = an.window("30d", "UTC", NOW, 7)
    assert w.clamped and w.start == NOW - timedelta(days=7)
    assert w.buckets[0] == datetime(2026, 10, 19, tzinfo=UTC)  # partial: holds the cutoff
    assert len(w.buckets) == 8 and an.previous(w) is None


def test_the_previous_period_is_as_long_and_inside_retention():
    w = an.window("24h", "UTC", NOW, 30)
    start, end = an.previous(w) or (None, None)
    assert end == w.start and end - start == w.end - w.start
    assert an.previous(an.window("24h", "UTC", NOW, 1)) is None  # would reach past retention


def trace_row(**over: Any) -> an.TraceRow:
    fields: dict[str, Any] = {
        "traces": 0,
        "running": 0,
        "errors": 0,
        "p50": None,
        "p95": None,
        "p99": None,
        "cost": 0.0,
        "unpriced_llm_spans": 0,
    }
    return an.TraceRow(**{**fields, **over})


class FakeAnalytics:
    """Canned rows; records the filter each call was given."""

    def __init__(self):
        self.totals: list[an.TraceRow] = [trace_row(), trace_row()]  # current, then previous
        self.buckets: list[an.TraceRow] = []
        self.kinds: list[an.KindRow] = []
        self.groups: list[an.GroupRow] = []
        self.costs: list[an.ModelCostRow] = []
        self.cost_buckets: list[an.ModelBucketRow] = []
        self.filters: list[TraceFilter] = []

    def trace_totals(self, project, f, complete_before):
        self.filters.append(f)
        return self.totals[len(self.filters) - 1]

    def trace_series(self, project, f, complete_before, bucket, tz):
        return self.trace_totals(project, f, complete_before), self.buckets

    def span_kinds(self, project, f, complete_before):
        return self.kinds

    def span_groups(self, project, f, complete_before, kind):
        self.filters.append(f)
        return self.groups

    def model_costs(self, project, f, complete_before, bucket, tz):
        return self.costs, self.cost_buckets


class Api:
    def __init__(self):
        self.store = FakeAnalytics()
        auth = FakeAuth.with_keys({"good": PROJECT})
        auth.add_key("spans-only", PROJECT, "ingest")
        app = create_app(
            Settings(trace_quiet_period_s=300),
            checks={},
            auth_store=auth,
            datasets=FakeDatasets(),
            pins=FakePins(),
            analytics_store=self.store,
        )
        self.c = TestClient(app)

    def get(self, path: str, expect=200, key="good", **params):
        r = self.c.get(
            f"/v1/analytics/{path}", params=params, headers={"Authorization": f"Bearer {key}"}
        )
        assert r.status_code == expect, r.text
        return r.json()


@pytest.fixture
def api() -> Api:
    return Api()


def test_volume_fills_empty_buckets_and_leaves_running_traces_out_of_the_error_rate(api: Api):
    api.store.totals = [trace_row(traces=10, running=2, errors=2), trace_row(traces=4, errors=1)]
    w = an.window("24h", "UTC", datetime.now(UTC), 30)
    api.store.buckets = [an.TraceRow(**{**trace_row(traces=3, running=3).model_dump(),
                                        "bucket": at(w.buckets[-1])})]  # fmt: skip
    api.store.kinds = [an.KindRow(kind="tool", spans=8, errors=2)]
    v = api.get("volume")
    assert v["totals"] == {
        "traces": 10, "running": 2, "completed": 8, "errors": 2, "error_rate": 0.25,
    }  # fmt: skip
    assert v["previous"]["error_rate"] == 0.25
    assert len(v["buckets"]) == 24 and v["buckets"][0]["traces"] == 0
    assert v["buckets"][-1]["running"] == 3 and v["buckets"][-1]["error_rate"] is None
    assert v["span_kinds"] == [{"kind": "tool", "spans": 8, "errors": 2, "error_rate": 0.25}]


def test_every_query_gets_the_window_and_production_traffic_by_default(api: Api):
    api.get("volume", range="7d", tag=["vip"])
    current, prior = api.store.filters
    assert current.until - current.since > timedelta(days=6) and current.tag == ["vip"]
    assert prior.until == current.since and not current.show_evals and not current.show_replays


def test_latency_turns_unmeasured_percentiles_into_null(api: Api):
    api.store.totals = [trace_row(traces=2, p50=float("nan"), p95=float("nan"), p99=None)] * 2
    lat = api.get("latency")
    assert lat["totals"] == {"completed": 2, "p50": None, "p95": None, "p99": None}


def test_cost_shows_the_top_models_and_folds_the_rest_into_other(api: Api):
    def cost(model: str, usd: float, calls=10, unpriced=0) -> an.ModelCostRow:
        return an.ModelCostRow(model=model, calls=calls, unpriced_calls=unpriced, cost=usd,
                               input_tokens=0, output_tokens=0, cache_read_tokens=0,
                               cache_write_tokens=0)  # fmt: skip

    api.store.costs = [cost(f"m{i}", float(i)) for i in range(1, 8)]
    api.store.costs.append(cost("mystery", 0.0, calls=4, unpriced=4))
    api.store.totals = [trace_row(traces=4, cost=28.0, unpriced_llm_spans=4)] * 2
    w = an.window("24h", "UTC", datetime.now(UTC), 30)
    b = at(w.buckets[-1])
    api.store.cost_buckets = [an.ModelBucketRow(bucket=b, model=f"m{i}", cost=float(i))
                              for i in range(1, 8)]  # fmt: skip
    c = api.get("cost")
    assert c["top_models"] == ["m7", "m6", "m5", "m4", "m3"]
    last = c["buckets"][-1]
    assert last["other"] == 3.0 and last["total"] == 28.0 and last["by_model"]["m7"] == 7.0
    assert c["buckets"][0] == {"start": c["buckets"][0]["start"], "by_model": dict.fromkeys(
        c["top_models"], 0.0), "other": 0.0, "total": 0.0}  # fmt: skip
    mystery = next(m for m in c["models"] if m["model"] == "mystery")
    assert mystery["unpriced_calls"] == 4 and mystery["cost_per_call"] is None
    assert c["totals"]["cost_per_trace"] == 7.0 and c["totals"]["unpriced_llm_spans"] == 4


def test_only_tools_with_enough_calls_are_ranked_slowest(api: Api):
    def group(name: str, calls: int, p95: float | None) -> an.GroupRow:
        return an.GroupRow(name=name, calls=calls, errors=1, p50=1.0, p95=p95)

    api.store.groups = [
        group("search", 500, 40.0),
        group("refund", 25, 900.0),
        group("rare", 3, 5000.0),
        group("never-ended", 30, float("nan")),
    ]
    t = api.get("tools")
    assert t["slowest"] == ["refund", "search"] and t["min_calls"] == 20
    assert {g["name"]: g["ranked"] for g in t["groups"]} == {
        "search": True, "refund": True, "rare": False, "never-ended": False,
    }  # fmt: skip
    assert api.get("models")["slowest"] == ["refund", "search"]


def test_parameters_are_checked(api: Api):
    api.get("volume", 422, range="90d")
    api.get("volume", 422, tz="Mars/Olympus")
    api.get("volume", 422, since="2026-10-01T00:00:00Z")  # the range sets the times
    api.get("volume", 422, ranges="24h")
    api.get("volume", 403, key="spans-only")
    api.get("volume", 401, key="nope")


def test_other_never_goes_below_zero(api: Api):
    def cost(model: str, usd: float) -> an.ModelCostRow:
        return an.ModelCostRow(model=model, calls=1, unpriced_calls=0, cost=usd,
                               input_tokens=0, output_tokens=0, cache_read_tokens=0,
                               cache_write_tokens=0)  # fmt: skip

    api.store.costs = [cost("a", 0.1), cost("b", 0.2)]
    b = at(an.window("24h", "UTC", datetime.now(UTC), 30).buckets[-1])
    api.store.cost_buckets = [an.ModelBucketRow(bucket=b, model=m, cost=c)
                              for m, c in (("a", 0.1), ("b", 0.2))]  # fmt: skip
    assert api.get("cost")["buckets"][-1]["other"] == 0.0  # 0.30000000000000004 - 0.1 - 0.2 < 0


class FailingClickHouse:
    def __init__(self, error: Exception):
        self.error = error

    def query(self, *args, **kwargs):
        raise self.error


@pytest.mark.parametrize(
    ("message", "error"),
    [
        ("Code: 36. DB::Exception: Cannot load time zone Asia/Nowhere", "Refused"),
        ("Code: 241. Memory limit exceeded", "Unavailable"),
    ],
)
def test_a_zone_clickhouse_lacks_is_the_caller_s_fault(message: str, error: str):
    from clickhouse_connect.driver.exceptions import DatabaseError

    from trayce_ingest.analytics_store import ClickHouseAnalytics

    store = ClickHouseAnalytics(Settings())
    store.__dict__["_clickhouse"] = FailingClickHouse(DatabaseError(message))
    f = TraceFilter(since=NOW - timedelta(days=1), until=NOW)
    with pytest.raises(Exception) as raised:
        store.trace_series(PROJECT, f, NOW, "hour", "Asia/Nowhere")
    assert type(raised.value).__name__ == error  # 422 and 503 (dataset_api's handlers)
