from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fake_datasets import FakeDatasets, FakePins
from fake_experiments import FakeExperiments, FakePrices
from fastapi.testclient import TestClient

from agenttrace.pricing import Price, PriceTable
from agenttrace_ingest.app import create_app
from agenttrace_ingest.settings import Settings

PROJECT = UUID("00000000-0000-0000-0000-0000000000aa")
OTHER = UUID("00000000-0000-0000-0000-0000000000bb")
STARTED = "2026-10-01T00:00:00Z"
PRICES = PriceTable({"m": [Price(datetime(2026, 1, 1, tzinfo=UTC), 4.0, 20.0)]})


class Keys:
    async def resolve(self, api_key: str) -> UUID | None:
        return {"good": PROJECT, "other": OTHER}.get(api_key)


class Api:
    """A published dataset `v1` with an output item and a trajectory item, and one experiment
    over it with an output scorer and a trajectory scorer."""

    def __init__(self):
        datasets = FakeDatasets()
        app = create_app(
            Settings(),
            checks={},
            keys=Keys(),
            datasets=datasets,
            pins=FakePins(),
            experiment_store=FakeExperiments(datasets),
            prices=FakePrices(PRICES),
        )
        self.c = TestClient(app)
        self.dataset = self.call("POST", "/v1/datasets", {"name": "support"}, status=201)["id"]
        items = f"/v1/datasets/{self.dataset}/items"
        self.answer = self.call("POST", items, {"input": "q1", "expected_output": "A"})["item_id"]
        trajectory = [{"tool": "lookup_order"}]
        self.steps = self.call("POST", items, {"input": "q2", "expected_trajectory": trajectory})[
            "item_id"
        ]
        self.call("POST", f"/v1/datasets/{self.dataset}/publish", status=200)

    def call(self, method: str, path: str, json=None, key="good", status=None):
        r = self.c.request(method, path, json=json, headers={"Authorization": f"Bearer {key}"})
        if status is not None:
            assert r.status_code == status, r.text
        return r.json() if r.content else None

    def new(self, **over) -> dict:
        return {
            "id": str(uuid4()),
            "dataset_id": self.dataset,
            "dataset_version": 1,
            "samples": 2,
            "scorers": [
                {"name": "exact", "type": "exact_match"},
                {
                    "name": "steps",
                    "type": "trajectory",
                    "threshold": 0.5,
                    "config": {"mode": "exact"},
                },
            ],
            "config": {"entry": "demo_agent:eval_entry"},
            "config_hash": "f" * 64,
            "git_commit": "abc123",
            **over,
        }

    def create(self, **over) -> dict:
        return self.call("POST", "/v1/experiments", self.new(**over), status=201)

    def sample(self, item_id: str, index: int, **over) -> dict:
        return {
            "item_id": item_id,
            "sample_index": index,
            "trace_id": "a" * 32,
            "started_at": STARTED,
            "latency_ms": 100.0,
            "output": "A",
            "trajectory": [{"tool": "lookup_order", "args": {"order_id": "1042"}}],
            "usage": [{"model": "m", "input_tokens": 1_000_000, "output_tokens": 100_000}],
            "scores": [],
            **over,
        }

    def upload(self, exp: str, *samples: dict, status=200) -> dict:
        body = {"samples": list(samples)}
        return self.call("POST", f"/v1/experiments/{exp}/samples", body, status=status)

    def finish(self, exp: str, status_: str, status=200, **extra) -> dict:
        body = {"status": status_, **extra}
        return self.call("POST", f"/v1/experiments/{exp}/finish", body, status=status)

    def upload_all(self, exp: str) -> None:
        """Every sample: `exact` passes on run 0 only, `steps` scores 0.5 (its threshold)."""
        for i in range(2):
            self.upload(
                exp,
                self.sample(self.answer, i, scores=[{"scorer": "exact", "score": 1.0 - i}]),
                self.sample(self.steps, i, scores=[{"scorer": "steps", "score": 0.5}]),
            )


@pytest.fixture
def api() -> Api:
    return Api()


def test_create_is_retryable_and_scoped_to_the_project(api: Api):
    body = api.new()
    created = api.call("POST", "/v1/experiments", body, status=201)
    assert (created["status"], created["item_count"], created["samples_uploaded"]) == (
        "running",
        2,
        0,
    )
    assert api.call("POST", "/v1/experiments", body, status=200)["id"] == created["id"]
    changed = {**body, "samples": 5}
    api.call("POST", "/v1/experiments", changed, status=409)
    api.call("GET", f"/v1/experiments/{created['id']}", key="other", status=404)
    assert api.call("GET", "/v1/experiments", key="other") == {"experiments": []}


def test_create_needs_a_published_version_and_valid_settings(api: Api):
    missing = api.call("POST", "/v1/experiments", api.new(dataset_version=9), status=404)
    assert "v9" in missing["detail"]["error"]
    api.call("POST", "/v1/experiments", api.new(dataset_id=str(uuid4())), status=404)
    twice = [{"name": "a", "type": "x"}, {"name": "a", "type": "y"}]
    bad = api.call("POST", "/v1/experiments", api.new(scorers=twice), status=422)
    assert "unique" in bad["detail"]["fields"]["request"]
    api.call("POST", "/v1/experiments", api.new(config_hash="nothex"), status=422)
    api.call("POST", "/v1/experiments", api.new(scorers=[]), status=422)


def test_uploads_are_upserts(api: Api):
    exp = api.create()["id"]
    first = api.sample(api.answer, 0, scores=[{"scorer": "exact", "score": 0.0}])
    assert api.upload(exp, first) == {"uploaded": 1}
    again = api.sample(api.answer, 0, scores=[{"scorer": "exact", "score": 1.0}])
    api.upload(exp, again)
    item = api.call("GET", f"/v1/experiments/{exp}/items/{api.answer}")
    assert [s["scores"] for s in item["samples"]] == [
        [{"scorer": "exact", "score": 1.0, "passed": True, "reason": None}]
    ]
    assert api.call("GET", f"/v1/experiments/{exp}")["samples_uploaded"] == 1


def test_uploads_are_checked_against_the_experiment(api: Api):
    exp = api.create()["id"]
    stranger = api.upload(exp, api.sample(str(uuid4()), 0), status=422)["detail"]
    assert "not in v1" in stranger["error"] and len(stranger["items"]) == 1
    too_far = api.upload(exp, api.sample(api.answer, 2), status=422)["detail"]["error"]
    assert "below" in too_far
    unknown = api.sample(api.answer, 0, scores=[{"scorer": "nope", "score": 1}])
    assert "nope" in api.upload(exp, unknown, status=422)["detail"]["error"]
    dup = api.sample(api.answer, 0)
    assert "twice" in api.upload(exp, dup, dup, status=422)["detail"]["error"]
    big = api.sample(api.answer, 0, output="x" * (1024 * 1024 + 1))
    assert "limit" in api.upload(exp, big, status=422)["detail"]["error"]
    nul = api.sample(api.answer, 0, error="bad\x00byte")
    assert "NUL" in api.upload(exp, nul, status=422)["detail"]["error"]
    out_of_range = api.sample(api.answer, 0, scores=[{"scorer": "exact", "score": 1.5}])
    api.upload(exp, out_of_range, status=422)
    api.upload(exp, status=422)  # an empty batch
    api.upload(str(uuid4()), api.sample(api.answer, 0), status=404)


def test_completing_needs_every_sample_and_freezes_the_results(api: Api):
    exp = api.create()["id"]
    api.upload(exp, api.sample(api.answer, 0))
    short = api.finish(exp, "completed", status=422)["detail"]["error"]
    assert short.startswith("1 of 4 samples uploaded")

    api.upload_all(exp)
    done = api.finish(exp, "completed")
    assert done["status"] == "completed" and done["finished_at"] is not None
    assert api.finish(exp, "completed")["status"] == "completed"  # a retried finish succeeds
    assert "already completed" in api.finish(exp, "failed", status=409)["detail"]["error"]
    frozen = api.upload(exp, api.sample(api.answer, 0), status=409)["detail"]["error"]
    assert "completed" in frozen


def test_a_runner_failure_can_finish_with_samples_missing(api: Api):
    exp = api.create()["id"]
    failed = api.finish(exp, "failed", error="could not import demo_agent")
    assert (failed["status"], failed["error"]) == ("failed", "could not import demo_agent")


def test_summary_reports_pass_rates_with_their_denominators(api: Api):
    exp = api.create()["id"]
    api.upload_all(exp)
    detail = api.call("GET", f"/v1/experiments/{exp}")
    stats = {s["scorer"]: s for s in detail["scorer_stats"]}
    # Each scorer only grades the item that has its expectation: 2 samples each, not 4.
    assert stats["exact"] | {"scorer": None} == {
        "scorer": None,
        "scored": 2,
        "passed": 1,
        "pass_rate": 0.5,
        "mean_score": 0.5,
        "pass_rate_by_sample": [1.0, 0.0],
    }
    assert (stats["steps"]["passed"], stats["steps"]["pass_rate"]) == (2, 1.0)  # 0.5 >= 0.5

    items = {i["item_id"]: i for i in detail["items"]}
    assert items[api.answer]["scores"] == {"exact": {"passed": 1, "scored": 2}}
    assert items[api.steps]["scores"] == {"steps": {"passed": 2, "scored": 2}}
    assert items[api.answer]["mean_latency_ms"] == 100.0


def test_errored_samples_are_counted(api: Api):
    exp = api.create()["id"]
    errored = api.sample(
        api.answer,
        0,
        output=None,
        error="TimeoutError: over 120 s",
        scores=[{"scorer": "exact", "score": 0, "reason": "agent raised TimeoutError"}],
    )
    api.upload(exp, errored)
    item = next(i for i in api.call("GET", f"/v1/experiments/{exp}")["items"])
    assert (item["item_id"], item["errored"]) == (api.answer, 1)
    sample = api.call("GET", f"/v1/experiments/{exp}/items/{api.answer}")["samples"][0]
    assert sample["error"].startswith("TimeoutError") and sample["output"] is None


def test_samples_are_priced_on_upload(api: Api):
    exp = api.create()["id"]
    two_models = [
        {"model": "m", "input_tokens": 1_000_000, "output_tokens": 100_000},
        {"model": "m", "input_tokens": 0, "output_tokens": 50_000},
    ]
    api.upload(exp, api.sample(api.answer, 0, usage=two_models))
    api.upload(exp, api.sample(api.answer, 1, usage=[{"model": "unknown", "input_tokens": 1}]))
    api.upload(exp, api.sample(api.steps, 0, usage=[]))
    samples = api.call("GET", f"/v1/experiments/{exp}/items/{api.answer}")["samples"]
    assert [s["cost_usd"] for s in samples] == [4.0 + 2.0 + 1.0, None]  # unknown, not low
    info = api.call("GET", f"/v1/experiments/{exp}")
    assert (info["cost_usd"], info["unpriced_samples"]) == (7.0, 1)


def test_item_detail_shows_the_versioned_item(api: Api):
    exp = api.create()["id"]
    item = api.call("GET", f"/v1/experiments/{exp}/items/{api.steps}")
    assert item["item"]["contents"]["expected_trajectory"] == [{"tool": "lookup_order"}]
    assert item["samples"] == []
    api.call("GET", f"/v1/experiments/{exp}/items/{uuid4()}", status=404)


def test_list_is_newest_first_and_filters_by_dataset(api: Api):
    first, second = api.create()["id"], api.create(name="tuned prompt")["id"]
    listed = api.call("GET", "/v1/experiments")["experiments"]
    assert [e["id"] for e in listed] == [second, first]
    assert listed[0]["name"] == "tuned prompt"
    filtered = api.call("GET", f"/v1/experiments?dataset_id={uuid4()}")
    assert filtered == {"experiments": []}
    api.call("GET", "/v1/experiments/not-a-uuid", status=404)
