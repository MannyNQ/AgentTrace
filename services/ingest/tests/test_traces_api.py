"""/v1/traces with in-memory storage: the list and its cursor, status at read time, one trace's
spans without their storage URIs, payloads, outcomes, and which traces are live (ADR-0031)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fake_auth import FakeAuth
from fake_datasets import FakeDatasets, FakePins
from fake_traces import T0, FakeTraces, span_row, summary_row
from fastapi.testclient import TestClient

from agenttrace_ingest.app import create_app
from agenttrace_ingest.settings import Settings

PROJECT = UUID("00000000-0000-0000-0000-0000000000aa")
OTHER = UUID("00000000-0000-0000-0000-0000000000bb")
A, B, C, D = ("a" * 32, "b" * 32, "c" * 32, "d" * 32)
ROOT, TOOL, TOOL2 = ("1" * 16, "2" * 16, "3" * 16)


class Api:
    def __init__(self):
        self.store = FakeTraces()
        auth = FakeAuth.with_keys({"good": PROJECT, "other": OTHER})
        auth.add_key("spans-only", PROJECT, "ingest")
        app = create_app(
            Settings(trace_quiet_period_s=300),
            checks={},
            auth_store=auth,
            datasets=FakeDatasets(),
            pins=FakePins(),
            trace_store=self.store,
        )
        self.c = TestClient(app)

    def get(self, path: str, key="good", status=200, **params):
        r = self.c.get(
            f"/v1/traces{path}", params=params, headers={"Authorization": f"Bearer {key}"}
        )
        assert r.status_code == status, r.text
        return r.json()


@pytest.fixture
def api() -> Api:
    return Api()


def test_the_list_is_newest_first_and_pages_by_cursor(api: Api):
    for i, t in enumerate((A, B, C)):
        api.store.add(PROJECT, summary_row(t, T0 + timedelta(minutes=i)))
    first = api.get("", limit=2)
    assert [t["trace_id"] for t in first["traces"]] == [C, B]
    cursor = first["next"]
    second = api.get(
        "", limit=2, before_start=cursor["start_time"], before_trace=cursor["trace_id"]
    )
    assert [t["trace_id"] for t in second["traces"]] == [A] and second["next"] is None


def test_replay_runs_are_hidden_unless_asked_for(api: Api):
    api.store.add(PROJECT, summary_row(A))
    api.store.add(PROJECT, summary_row(B, T0 + timedelta(seconds=1), replay_of=A))
    assert [t["trace_id"] for t in api.get("")["traces"]] == [A]
    shown = api.get("", show_replays=True)["traces"]
    assert [(t["trace_id"], t["replay_of"]) for t in shown] == [(B, A), (A, "")]


def test_another_projects_traces_are_invisible(api: Api):
    api.store.add(OTHER, summary_row(A), [span_row(ROOT)])
    assert api.get("")["traces"] == []
    api.get(f"/{A}", status=404)
    api.get(f"/{A}/spans/{ROOT}/input", status=404)
    assert api.get(f"/{A}", key="other")["summary"]["trace_id"] == A


def test_an_ingest_key_can_t_read_traces(api: Api):
    api.get("", key="spans-only", status=403)


def test_status_and_latency_are_decided_on_read(api: Api):
    now = datetime.now(UTC)
    rows = {
        A: summary_row(A, root_error=True),
        B: summary_row(B, root_ended=False),  # quiet long enough: the root never closed
        C: summary_row(C, now - timedelta(seconds=5), root_ended=False, last_ingested_at=now),
        D: summary_row(D, has_root=False, root_name="", root_ended=False),
    }
    for row in rows.values():
        api.store.add(PROJECT, row)
    got = {t["trace_id"]: t for t in api.get("")["traces"]}
    assert {t: got[t]["status"] for t in rows} == {A: "error", B: "error", C: "running", D: "error"}
    assert got[A]["latency_ms"] == 250
    assert got[D]["root_name"] == "(root span missing)"


def test_cost_is_null_when_nothing_was_priced(api: Api):
    api.store.add(PROJECT, summary_row(A, llm_spans=2, unpriced_llm_spans=2, cost_usd=0.0))
    api.store.add(PROJECT, summary_row(B, llm_spans=2, unpriced_llm_spans=1, cost_usd=0.01))
    got = {t["trace_id"]: t for t in api.get("")["traces"]}
    assert got[A]["cost_usd"] is None and got[B]["cost_usd"] == 0.01


def test_a_trace_s_spans_show_offloading_but_not_where(api: Api):
    api.store.add(
        PROJECT,
        summary_row(A),
        [
            span_row(ROOT, input_preview='{"q": 1', input_uri="s3://payloads/x/in"),
            span_row(TOOL, T0 + timedelta(milliseconds=1), parent_span_id=ROOT, kind="tool"),
        ],
    )
    detail = api.get(f"/{A}")
    root, tool = detail["spans"]
    assert root["input_offloaded"] is True and root["output_offloaded"] is False
    assert "input_uri" not in root and "s3://" not in str(detail)
    assert tool["parent_span_id"] == ROOT


def test_payloads_come_from_the_preview_or_object_storage(api: Api):
    api.store.objects["s3://payloads/x/in"] = '{"q": "the whole thing"}'
    api.store.add(
        PROJECT,
        summary_row(A),
        [
            span_row(
                ROOT,
                input_preview='{"q": "the',
                input_uri="s3://payloads/x/in",
                output_preview='"done"',
            )
        ],
    )
    assert api.get(f"/{A}/spans/{ROOT}/input") == {"text": '{"q": "the whole thing"}'}
    assert api.get(f"/{A}/spans/{ROOT}/output") == {"text": '"done"'}
    assert api.store.reads == ["s3://payloads/x/in"]
    api.get(f"/{A}/spans/{TOOL}/input", status=404)
    api.get(f"/{A}/spans/{ROOT}/metadata", status=404)


def test_an_outcome_is_the_tool_calls_and_the_root_output(api: Api):
    api.store.add(
        PROJECT,
        summary_row(A),
        [
            span_row(ROOT, status="error", error_type="Timeout", output_preview='{"answer": 42}'),
            span_row(
                TOOL2,
                T0 + timedelta(milliseconds=2),
                parent_span_id=ROOT,
                kind="tool",
                name="refund",
                input_preview="not json",
            ),  # fmt: skip
            span_row(
                TOOL,
                T0 + timedelta(milliseconds=1),
                parent_span_id=ROOT,
                kind="tool",
                name="lookup",
                input_preview='{"order": 1042}',
            ),  # fmt: skip
        ],
    )
    assert api.get(f"/{A}/outcome") == {
        "trajectory": [{"tool": "lookup", "args": {"order": 1042}}, {"tool": "refund"}],
        "output": {"answer": 42},
        "root_error": "Timeout",
    }


def test_live_names_the_traces_that_still_exist_in_order(api: Api):
    api.store.add(PROJECT, summary_row(A))
    api.store.add(PROJECT, summary_row(C))
    api.store.add(OTHER, summary_row(B))
    r = api.c.post(
        "/v1/traces/live",
        json={"trace_ids": [C, B, A]},
        headers={"Authorization": "Bearer good"},
    )
    assert r.json() == {"trace_ids": [C, A]}


def test_malformed_ids_are_not_found(api: Api):
    api.get("/not-a-trace", status=404)
    api.get(f"/{A}/spans/xyz/input", status=404)


def test_the_page_size_is_bounded(api: Api):
    for i in range(3):
        api.store.add(PROJECT, summary_row(f"{i:032x}", T0 + timedelta(seconds=i)))
    assert len(api.get("", limit=0)["traces"]) == 1
    assert len(api.get("", limit=10_000)["traces"]) == 3


def test_clickhouse_datetimes_are_utc_whatever_the_local_zone():
    """clickhouse_connect hands back naive datetimes when the local zone is UTC (container, CI)
    and aware ones elsewhere; both must come out aware, or status math fails."""
    from datetime import timezone

    from agenttrace_ingest.trace_store import _span, _summary, utc

    naive = datetime(2026, 10, 1, 12, 0)
    paris = datetime(2026, 10, 1, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    assert utc(naive) == utc(paris) == datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    row = summary_row(A)
    summary = _summary(
        [row.trace_id, naive, naive, naive, 1, 0.0, 0, 0, 1, 1, 0, naive, "r", "", "", [], "", "",
         0]
    )  # fmt: skip
    assert summary.start_time.tzinfo is not None and summary.root_end_time.tzinfo is not None
    span = span_row(ROOT)
    values = [*span.model_dump().values()]
    values[4], values[5] = naive, None
    assert _span(values).start_time.tzinfo is not None


class Failing:
    """A ClickHouse or S3 client whose every call raises `error`."""

    def __init__(self, error: Exception):
        self.error = error

    def query(self, *args, **kwargs):
        raise self.error

    def get_object(self, *args, **kwargs):
        raise self.error


def outage_api(error: Exception) -> TestClient:
    from agenttrace_ingest.trace_store import ClickHouseTraces

    store = ClickHouseTraces(Settings())
    store.__dict__["_clickhouse"] = Failing(error)  # in place of the lazily built client
    app = create_app(
        Settings(),
        checks={},
        auth_store=FakeAuth.with_keys({"good": PROJECT}),
        datasets=FakeDatasets(),
        pins=FakePins(),
        trace_store=store,
    )
    return TestClient(app, headers={"Authorization": "Bearer good"})


def test_a_clickhouse_outage_is_503_with_the_reason():
    from clickhouse_connect.driver.exceptions import OperationalError

    c = outage_api(OperationalError("Error HTTPConnectionPool(host='clickhouse'): refused"))
    for r in (
        c.get("/v1/traces"),
        c.get(f"/v1/traces/{A}"),
        c.get(f"/v1/traces/{A}/outcome"),
        c.post("/v1/traces/live", json={"trace_ids": [A]}),
    ):
        assert r.status_code == 503, r.text
        assert r.json()["detail"]["error"].startswith("ClickHouse can't be read (OperationalError")


def test_a_missing_migrated_column_says_the_migration_is_pending():
    from clickhouse_connect.driver.exceptions import DatabaseError

    error = DatabaseError("Code: 47. DB::Exception: Unknown expression identifier `replay_of`")
    r = outage_api(error).get("/v1/traces")
    assert r.status_code == 503
    assert "doesn't have the `replay_of` column yet" in r.json()["detail"]["error"]


def test_an_object_storage_outage_is_unavailable():
    from botocore.exceptions import EndpointConnectionError

    from agenttrace_ingest.trace_store import ClickHouseTraces
    from agenttrace_ingest.traces import Unavailable

    store = ClickHouseTraces(Settings())
    store.__dict__["_s3"] = Failing(EndpointConnectionError(endpoint_url="http://seaweedfs:8333"))
    with pytest.raises(Unavailable, match="object storage can't be read"):
        store.payload("s3://payloads/x/in")


def test_half_a_cursor_is_refused(api: Api):
    r = api.get("", status=422, before_start="2026-10-01T12:00:00Z")
    assert "both before_start and before_trace" in r["detail"]["error"]
    api.get("", status=422, before_trace=A)


GOOD = {"Authorization": "Bearer good"}


def ids(page: dict) -> set[str]:
    return {t["trace_id"] for t in page["traces"]}


def test_only_production_traffic_is_listed_by_default(api: Api):
    from agenttrace.eval.results import EVAL_TAG

    api.store.add(PROJECT, summary_row(A))
    api.store.add(PROJECT, summary_row(B, tags=[EVAL_TAG]))
    api.store.add(PROJECT, summary_row(C, replay_of=A))
    assert ids(api.get("")) == {A}
    assert ids(api.get("", show_evals=True)) == {A, B}
    assert ids(api.get("", show_evals=True, show_replays=True)) == {A, B, C}


def test_the_filter_narrows_by_time_tags_user_and_service(api: Api):
    api.store.add(PROJECT, summary_row(A, T0, tags=["vip", "eu"], user_id="u1"))
    api.store.add(PROJECT, summary_row(B, T0 + timedelta(hours=1), tags=["vip"]))
    api.store.add(PROJECT, summary_row(C, T0 + timedelta(hours=2), service_name="support"))
    at = lambda h: (T0 + timedelta(hours=h)).isoformat()  # noqa: E731
    assert ids(api.get("", since=at(1))) == {B, C}
    assert ids(api.get("", until=at(1))) == {A}  # until is exclusive
    assert ids(api.get("", tag=["vip", "eu"])) == {A}
    assert ids(api.get("", tag=["vip"])) == {A, B}
    assert ids(api.get("", user_id="u1")) == {A}
    assert ids(api.get("", service_name="support")) == {C}


def test_the_filter_matches_status_as_the_list_shows_it(api: Api):
    now = datetime.now(UTC)
    api.store.add(PROJECT, summary_row(A))
    api.store.add(PROJECT, summary_row(B, root_ended=False))  # went quiet: error
    api.store.add(PROJECT, summary_row(C, now, root_ended=False, last_ingested_at=now))
    for status, want in (("ok", A), ("error", B), ("running", C)):
        # `status` is the helper's expected HTTP status, so the parameter goes in by hand.
        page = api.c.get("/v1/traces", params={"status": status}, headers=GOOD).json()
        assert ids(page) == {want} and page["traces"][0]["status"] == status


def test_the_filter_matches_traces_by_their_spans(api: Api):
    api.store.add(
        PROJECT, summary_row(A), [span_row(ROOT), span_row(TOOL, kind="tool", name="refund")]
    )
    api.store.add(
        PROJECT,
        summary_row(B),
        [span_row(ROOT), span_row(TOOL, kind="tool", name="refund", status="error")],
    )
    api.store.add(PROJECT, summary_row(C), [span_row(ROOT)])
    assert ids(api.get("", span_kind="tool")) == {A, B}
    assert ids(api.get("", span_name="refund", span_error=True)) == {B}
    assert ids(api.get("", span_kind="llm")) == set()
    api.store.add(PROJECT, summary_row(D), [span_row(ROOT), span_row(TOOL, kind="llm", model="m1")])
    assert ids(api.get("", span_model="m1")) == {D}


def test_a_malformed_filter_is_refused(api: Api):
    api.get("", status=422, since="2026-10-01T12:00:00")  # no time zone
    api.get("", status=422, since="2026-10-02T00:00:00Z", until="2026-10-01T00:00:00Z")
    api.get("", status=422, span_error=True)
    api.get("", status=422, statuses="ok")  # an unknown parameter isn't ignored
    api.get("", status=422, span_kind="database")
