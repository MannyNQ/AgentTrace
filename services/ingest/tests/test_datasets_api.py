import json
from uuid import UUID

import pytest
from fake_auth import FakeAuth
from fake_datasets import FakeDatasets, FakePins, FakeTraces
from fastapi.testclient import TestClient

from agenttrace_ingest.app import create_app
from agenttrace_ingest.datasets import MAX_JSON_BYTES, Refused, pin_pending
from agenttrace_ingest.settings import Settings
from agenttrace_ingest.trace_inputs import decode_payload

PROJECT = UUID("00000000-0000-0000-0000-0000000000aa")
OTHER = UUID("00000000-0000-0000-0000-0000000000bb")
TRACE = "a" * 32
BIG_TRACE = "b" * 32
CUT_TRACE = "c" * 32


KEYS = {"good": PROJECT, "other": OTHER}


class Api:
    def __init__(self):
        traces = FakeTraces(
            {
                TRACE: {"question": "Where is order 1042?"},
                BIG_TRACE: "x" * (MAX_JSON_BYTES + 1),
                CUT_TRACE: Refused("the SDK cut this trace's input"),
            }
        )
        self.store = FakeDatasets()
        self.pins = FakePins()
        app = create_app(
            Settings(),
            checks={},
            auth_store=FakeAuth.with_keys(KEYS),
            datasets=self.store,
            trace_inputs=traces,
            pins=self.pins,
        )
        self.c = TestClient(app)

    def call(self, method: str, path: str, json=None, key="good", status=None):
        r = self.c.request(
            method, f"/v1/datasets{path}", json=json, headers={"Authorization": f"Bearer {key}"}
        )
        if status is not None:
            assert r.status_code == status, r.text
        return r.json() if r.content else None

    def dataset(self, name="refunds") -> str:
        return self.call("POST", "", {"name": name}, status=201)["id"]

    def item(self, ds: str, **contents) -> dict:
        return self.call("POST", f"/{ds}/items", {"input": "q", **contents}, status=201)

    def edit(self, ds: str, item: dict, status=200, **contents) -> dict:
        body = {"rev": item["rev"], "contents": {"input": "q", **contents}}
        return self.call("PATCH", f"/{ds}/items/{item['item_id']}", body, status=status)


@pytest.fixture
def api() -> Api:
    return Api()


def test_needs_an_api_key_and_scopes_to_its_project(api: Api):
    ds = api.dataset()
    assert api.c.get("/v1/datasets").status_code == 401
    assert api.call("GET", "", key="other") == {"datasets": []}
    assert api.call("GET", f"/{ds}", key="other", status=404)


def test_names_are_unique_slugs_and_renamable(api: Api):
    ds = api.dataset("refunds")
    api.call("POST", "", {"name": "refunds"}, status=409)
    api.call("POST", "", {"name": "Has Spaces"}, status=422)
    other = api.dataset("other")
    api.call("PATCH", f"/{other}", {"name": "refunds"}, status=409)
    renamed = api.call("PATCH", f"/{ds}", {"name": "refund-failures"}, status=200)
    assert (renamed["id"], renamed["name"]) == (ds, "refund-failures")


def test_archived_datasets_are_hidden_and_read_only(api: Api):
    ds = api.dataset()
    api.call("PATCH", f"/{ds}", {"archived": True}, status=200)
    assert api.call("GET", "")["datasets"] == []
    assert [d["id"] for d in api.call("GET", "?include_archived=true")["datasets"]] == [ds]
    assert (
        "archived"
        in api.call("POST", f"/{ds}/items", {"input": "q"}, status=422)["detail"]["error"]
    )
    api.call("PATCH", f"/{ds}", {"archived": False}, status=200)
    api.item(ds)
    api.call("PATCH", f"/{ds}", {"archived": True}, status=200)
    path = f"/{ds}/items/from-trace"
    assert "archived" in api.call("POST", path, {"trace_id": TRACE}, status=422)["detail"]["error"]


def test_validation_errors_name_their_fields_and_bad_ids_are_404(api: Api):
    ds = api.dataset()
    detail = api.call("POST", f"/{ds}/items", {"input": "q", "metadata": {"n": 1}}, status=422)[
        "detail"
    ]
    assert detail["error"] == "some fields are invalid"
    assert set(detail["fields"]) == {"metadata"}
    item = api.item(ds)
    body = {"rev": 1, "contents": {"input": "q", "expected_trajectory": [{"x": 1}]}}
    edit = api.call("PATCH", f"/{ds}/items/{item['item_id']}", body, status=422)["detail"]
    assert set(edit["fields"]) == {"expected_trajectory"}
    api.call("GET", "/not-a-uuid", status=404)


def test_deleting_keeps_published_items_and_drops_unpublished_ones(api: Api):
    ds = api.dataset()
    published = api.item(ds, expected_output="p")
    api.call("POST", f"/{ds}/publish", status=200)
    draft_only = api.item(ds)
    api.call("DELETE", f"/{ds}/items/{published['item_id']}?rev=1", status=204)
    api.call("DELETE", f"/{ds}/items/{draft_only['item_id']}?rev=1", status=204)
    stored = api.store.datasets[UUID(ds)].items
    assert set(map(str, stored)) == {published["item_id"]}
    assert stored[UUID(published["item_id"])].deleted


def test_bodies_over_the_limit_are_413_even_without_content_length():
    c = TestClient(
        create_app(
            Settings(max_request_bytes=100),
            checks={},
            auth_store=FakeAuth.with_keys(KEYS),
            datasets=FakeDatasets(),
        )
    )
    headers = {"Authorization": "Bearer good", "Content-Type": "application/json"}
    r = c.post(
        "/v1/datasets",
        content=iter([b'{"name": "x", "description": "', b"d" * 200, b'"}']),
        headers=headers,
    )
    assert r.status_code == 413, r.text
    assert "content-length" not in {k.lower() for k in r.request.headers}


def test_item_contents_are_validated(api: Api):
    ds = api.dataset()
    api.call("POST", f"/{ds}/items", {"input": None}, status=422)
    api.call("POST", f"/{ds}/items", {"input": "q", "expected_trajectory": [{"x": 1}]}, status=422)
    api.call("POST", f"/{ds}/items", {"input": "q", "metadata": {"n": 1}}, status=422)
    api.call("POST", f"/{ds}/items", {"input": "q", "surprise": 1}, status=422)
    api.call("POST", f"/{ds}/items", {"input": "x" * MAX_JSON_BYTES}, status=422)
    item = api.item(ds, expected_trajectory=[{"tool": "lookup_order", "args": {"id": "1"}}])
    assert item["contents"]["expected_trajectory"] == [
        {"tool": "lookup_order", "args": {"id": "1"}}
    ]


def test_edits_add_revisions_and_stale_revs_conflict(api: Api):
    ds = api.dataset()
    item = api.item(ds)
    edited = api.edit(ds, item, expected_output="Shipped")
    assert (edited["item_id"], edited["rev"]) == (item["item_id"], 2)
    stale = api.edit(ds, item, status=409, expected_output="Lost")
    assert stale["detail"]["rev"] == 2 and "reload" in stale["detail"]["error"]
    api.call("DELETE", f"/{ds}/items/{item['item_id']}?rev=1", status=409)
    api.call("DELETE", f"/{ds}/items/{item['item_id']}?rev=2", status=204)
    assert api.call("GET", f"/{ds}/items")["items"] == []


def test_from_trace_copies_the_input_once(api: Api):
    ds = api.dataset()
    item = api.call("POST", f"/{ds}/items/from-trace", {"trace_id": TRACE}, status=201)
    assert item["source_trace_id"] == TRACE
    assert item["contents"] == {
        "input": {"question": "Where is order 1042?"},
        "expected_output": None,
        "expected_trajectory": None,
        "metadata": {},
    }
    again = api.call("POST", f"/{ds}/items/from-trace", {"trace_id": TRACE}, status=409)
    assert again["detail"]["item_id"] == item["item_id"]

    api.call("DELETE", f"/{ds}/items/{item['item_id']}?rev=1", status=204)
    api.call("POST", f"/{ds}/items/from-trace", {"trace_id": TRACE}, status=201)


def test_from_trace_refusals(api: Api):
    ds = api.dataset()
    path = f"/{ds}/items/from-trace"
    assert (
        "expired" in api.call("POST", path, {"trace_id": "d" * 32}, status=404)["detail"]["error"]
    )
    assert "cut" in api.call("POST", path, {"trace_id": CUT_TRACE}, status=422)["detail"]["error"]
    assert "limit" in api.call("POST", path, {"trace_id": BIG_TRACE}, status=422)["detail"]["error"]
    api.call("POST", path, {"trace_id": "not-hex"}, status=422)


def test_publish_rules(api: Api):
    ds = api.dataset()
    assert "nothing to publish" in api.call("POST", f"/{ds}/publish", status=422)["detail"]["error"]
    a = api.item(ds)
    refused = api.call("POST", f"/{ds}/publish", status=422)["detail"]
    assert refused["items"] == [a["item_id"]]
    assert api.call("GET", f"/{ds}/items")["items"][0]["needs_expectation"] is True

    a = api.edit(ds, a, expected_output="Shipped")
    assert api.call("POST", f"/{ds}/publish", status=200) == {"version": 1}
    assert "nothing to publish" in api.call("POST", f"/{ds}/publish", status=422)["detail"]["error"]
    assert api.call("GET", f"/{ds}")["latest_version"] == 1

    # Only new or changed items need an expectation.
    b = api.item(ds)
    refused = api.call("POST", f"/{ds}/publish", status=422)["detail"]
    assert refused["items"] == [b["item_id"]]
    statuses = {i["item_id"]: i["status"] for i in api.call("GET", f"/{ds}/items")["items"]}
    assert statuses == {a["item_id"]: "unchanged", b["item_id"]: "new"}


def test_version_diff(api: Api):
    ds = api.dataset()
    keep = api.item(ds, expected_output="k")
    change = api.item(ds, expected_output="v1")
    drop = api.item(ds, expected_output="d")
    api.call("POST", f"/{ds}/publish", status=200)

    api.edit(ds, change, expected_output="v2")
    api.call("DELETE", f"/{ds}/items/{drop['item_id']}?rev=1", status=204)
    added = api.item(ds, expected_output="a")
    assert api.call("POST", f"/{ds}/publish", status=200) == {"version": 2}

    v2 = api.call("GET", f"/{ds}/versions/2", status=200)
    changes = {i["item_id"]: i["change"] for i in v2["items"]}
    assert changes == {
        keep["item_id"]: "unchanged",
        change["item_id"]: "changed",
        added["item_id"]: "added",
    }
    assert [(r["item_id"], r["contents"]["expected_output"]) for r in v2["removed"]] == [
        (drop["item_id"], "d")
    ]
    # Versions keep the revision they pointed at.
    v1 = api.call("GET", f"/{ds}/versions/1", status=200)
    by_id = {i["item_id"]: i for i in v1["items"]}
    assert by_id[change["item_id"]]["contents"]["expected_output"] == "v1"
    assert all(i["change"] == "added" for i in v1["items"])
    assert [v["version"] for v in api.call("GET", f"/{ds}/versions")["versions"]] == [2, 1]
    api.call("GET", f"/{ds}/versions/3", status=404)


def test_name_conflicts_point_at_the_holder_even_when_archived(api: Api):
    ds = api.dataset("refunds")
    api.call("PATCH", f"/{ds}", {"archived": True}, status=200)
    assert api.call("POST", "", {"name": "refunds"}, status=409)["detail"]["dataset_id"] == ds
    other = api.dataset("other")
    renamed = api.call("PATCH", f"/{other}", {"name": "refunds"}, status=409)
    assert renamed["detail"]["dataset_id"] == ds


def test_create_with_a_trace_starts_the_draft_or_creates_nothing(api: Api):
    created = api.call("POST", "", {"name": "refunds", "trace_id": TRACE}, status=201)
    assert created["draft_items"] == 1
    items = api.call("GET", f"/{created['id']}/items")["items"]
    assert [i["source_trace_id"] for i in items] == [TRACE]

    refused = api.call("POST", "", {"name": "cut", "trace_id": CUT_TRACE}, status=422)
    assert "cut" in refused["detail"]["error"]
    api.call("POST", "", {"name": "gone", "trace_id": "d" * 32}, status=404)
    names = [d["name"] for d in api.call("GET", "?include_archived=true")["datasets"]]
    assert names == ["refunds"]


def test_contents_must_be_storable_as_jsonb(api: Api):
    ds = api.dataset()
    item = f"/{ds}/items"
    cases = [
        ({"input": float("nan")}, "input"),
        ({"input": "q", "expected_output": {"x": float("inf")}}, "expected_output"),
        ({"input": "a\x00b"}, "input"),
        ({"input": {"k\x00": 1}}, "input"),
        ({"input": "q", "metadata": {"k": "v\x00"}}, "metadata"),
        (
            {"input": "q", "expected_trajectory": [{"tool": "t", "args": {"a": "\x00"}}]},
            "expected_trajectory",
        ),
    ]
    for body, field in cases:
        # Python's json module writes NaN and Infinity, as some agent SDKs do.
        r = api.c.post(
            f"/v1/datasets{item}",
            content=json.dumps(body),
            headers={"Authorization": "Bearer good", "Content-Type": "application/json"},
        )
        assert r.status_code == 422, (body, r.text)
        assert set(r.json()["detail"]["fields"]) == {field}, r.text
    api.call("PATCH", f"/{ds}", {"description": "a\x00b"}, status=422)
    api.call("POST", item, {"input": "back\\u0000slash text is fine"}, status=201)


def test_from_trace_refuses_inputs_jsonb_cannot_hold():
    nan_trace = "e" * 32
    traces = FakeTraces({nan_trace: {"score": float("nan")}})
    c = TestClient(
        create_app(
            Settings(),
            checks={},
            auth_store=FakeAuth.with_keys(KEYS),
            datasets=FakeDatasets(),
            trace_inputs=traces,
            pins=FakePins(),
        )
    )
    headers = {"Authorization": "Bearer good"}
    ds = c.post("/v1/datasets", json={"name": "x"}, headers=headers).json()["id"]
    r = c.post(f"/v1/datasets/{ds}/items/from-trace", json={"trace_id": nan_trace}, headers=headers)
    assert r.status_code == 422 and "NaN" in r.json()["detail"]["error"], r.text


def test_adding_a_trace_pins_it(api: Api):
    ds = api.dataset()
    api.call("POST", f"/{ds}/items/from-trace", {"trace_id": TRACE}, status=201)
    api.call("POST", "", {"name": "second", "trace_id": TRACE}, status=201)
    assert api.pins.pinned == [(PROJECT, TRACE), (PROJECT, TRACE)]
    assert api.store.unpinned(10) == []


def test_a_failed_pin_keeps_the_item_and_is_retried_on_startup(api: Api):
    api.pins.down = True
    ds = api.dataset()
    item = api.call("POST", f"/{ds}/items/from-trace", {"trace_id": TRACE}, status=201)
    assert item["source_trace_id"] == TRACE
    assert api.store.unpinned(10) == [(PROJECT, TRACE)]

    assert pin_pending(api.store, api.pins) == 0  # still down: stops instead of spinning
    api.pins.down = False
    assert pin_pending(api.store, api.pins) == 1
    assert api.pins.pinned == [(PROJECT, TRACE)]
    assert pin_pending(api.store, api.pins) == 0


def test_pin_pending_batches_by_project():
    store, pins = FakeDatasets(), FakePins()
    for project, trace in [(PROJECT, "1" * 32), (OTHER, "2" * 32), (PROJECT, "3" * 32)]:
        ds = store.create_dataset(project, f"d{trace[0]}", "")
        with store.open(project, ds, lock=True) as tx:
            tx.insert_item(trace)
    assert pin_pending(store, pins, batch=2) == 3
    assert sorted(pins.pinned) == sorted(
        [(PROJECT, "1" * 32), (OTHER, "2" * 32), (PROJECT, "3" * 32)]
    )


@pytest.mark.parametrize(
    "stored, expected",
    [
        ('{"question": "q"}', {"question": "q"}),
        ("[1, 2]", [1, 2]),
        ("123", "123"),
        ("true", "true"),
        ('"quoted"', '"quoted"'),
        ("plain text", "plain text"),
    ],
)
def test_stored_payloads_decode_objects_and_arrays_only(stored, expected):
    assert decode_payload(stored) == expected
