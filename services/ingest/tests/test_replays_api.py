from datetime import UTC, datetime
from uuid import UUID, uuid4

from fake_auth import FakeAuth
from fake_datasets import FakeDatasets, FakePins, FakeTraces
from fake_experiments import FakePrices
from fake_replays import FakeReplays
from fastapi.testclient import TestClient

from trayce.eval.results import SpanRef
from trayce.pricing import Price, PriceTable
from trayce_ingest.app import create_app
from trayce_ingest.errors import Refused
from trayce_ingest.settings import Settings

PROJECT = UUID("00000000-0000-0000-0000-0000000000aa")
OTHER = UUID("00000000-0000-0000-0000-0000000000bb")
STARTED = "2026-10-01T00:00:00Z"
PRICES = PriceTable({"m": [Price(datetime(2026, 1, 1, tzinfo=UTC), 4.0, 20.0)]})
FAILED = "a" * 32  # a production trace that went wrong
REPLAY_RUN = "b" * 32  # a replay run of FAILED
TRUNCATED = "c" * 32
UNRELATED = "d" * 32
FORK_RUN = "f" * 32  # a fork of REPLAY_RUN at its step STEP
STEP = "9" * 16


KEYS = {"good": PROJECT, "other": OTHER}


class Api:
    """A dataset whose draft holds an item made from FAILED, with an expected trajectory."""

    def __init__(self):
        datasets = FakeDatasets()
        traces = FakeTraces(
            {
                FAILED: {"question": "refund order 42"},
                REPLAY_RUN: {"question": "refund order 42"},
                TRUNCATED: Refused("the SDK cut this trace's input to fit a request"),
                UNRELATED: {"question": "hi"},
                FORK_RUN: {"question": "refund order 42"},
            },
            replay_of={
                REPLAY_RUN: SpanRef(trace_id=FAILED, span_id=FAILED[:16]),
                FORK_RUN: SpanRef(trace_id=REPLAY_RUN, span_id=STEP),
            },
            project=PROJECT,
            origins={FORK_RUN: SpanRef(trace_id=FAILED, span_id=FAILED[:16])},
        )
        self.traces = traces
        app = create_app(
            Settings(),
            checks={},
            auth_store=FakeAuth.with_keys(KEYS),
            datasets=datasets,
            trace_inputs=traces,
            pins=FakePins(),
            prices=FakePrices(PRICES),
            replay_store=FakeReplays(datasets),
            trace_roots=traces,
        )
        self.c = TestClient(app)
        body = {"name": "support", "trace_id": FAILED}
        self.dataset = self.call("POST", "/v1/datasets", body, status=201)["id"]
        draft = self.call("GET", f"/v1/datasets/{self.dataset}/items", status=200)["items"]
        self.item = draft[0]["item_id"]
        edit = {
            "rev": 1,
            "contents": {**draft[0]["contents"], "expected_trajectory": [{"tool": "escalate"}]},
        }
        self.call("PATCH", f"/v1/datasets/{self.dataset}/items/{self.item}", edit, status=200)

    def call(self, method: str, path: str, json=None, key="good", status=None, params=None):
        r = self.c.request(
            method, path, json=json, params=params, headers={"Authorization": f"Bearer {key}"}
        )
        if status is not None:
            assert r.status_code == status, r.text
        return r.json() if r.content else None

    def new(self, scored=True, **over) -> dict:
        body = {
            "id": str(uuid4()),
            "replay_of": {"trace_id": FAILED, "span_id": FAILED[:16]},
            "runs": 2,
            "git_commit": "abc123",
            "git_dirty": True,
        }
        if scored:
            body |= {
                "item_id": self.item,
                "item_rev": 2,
                "scorers": [{"name": "steps", "type": "trajectory", "config": {"mode": "exact"}}],
                "config": {"entry": "demo_agent:eval_entry"},
                "config_hash": "f" * 64,
            }
        return body | over

    def create(self, **over) -> dict:
        return self.call("POST", "/v1/replays", self.new(**over), status=201)

    def run(self, index: int, score: float | None = 1.0, **over) -> dict:
        r = {
            "run_index": index,
            "trace_id": f"{index:032x}",
            "started_at": STARTED,
            "latency_ms": 10.0,
            "output": "escalated",
            "trajectory": [{"tool": "escalate", "args": {}}],
            "usage": [{"model": "m", "input_tokens": 1_000_000}],
        }
        if score is not None:
            r["scores"] = [{"scorer": "steps", "score": score, "reason": None}]
        return r | over

    def upload(self, replay_id: str, *runs: dict, status=200):
        return self.call(
            "POST", f"/v1/replays/{replay_id}/runs", {"runs": list(runs)}, status=status
        )

    def finish(self, replay_id: str, status="completed", code=200):
        return self.call("POST", f"/v1/replays/{replay_id}/finish", {"status": status}, status=code)


def test_trace_input_gives_the_root_span_and_its_full_input():
    api = Api()
    got = api.call("GET", f"/v1/traces/{FAILED}/input", status=200)
    assert got == {
        "trace_id": FAILED,
        "span_id": FAILED[:16],
        "input": {"question": "refund order 42"},
        "replay_of": None,
    }


def test_a_replay_runs_input_names_the_original_it_re_ran():
    got = Api().call("GET", f"/v1/traces/{REPLAY_RUN}/input", status=200)
    assert got["replay_of"] == {"trace_id": FAILED, "span_id": FAILED[:16]}


def test_trace_input_refuses_a_truncated_input_and_a_missing_trace():
    api = Api()
    assert "cut" in api.call("GET", f"/v1/traces/{TRUNCATED}/input", status=422)["detail"]["error"]
    api.call("GET", f"/v1/traces/{'e' * 32}/input", status=404)
    api.call("GET", "/v1/traces/not-a-trace/input", status=404)
    api.call("GET", f"/v1/traces/{FAILED}/input", key="bad", status=401)


def test_a_scored_replay_runs_start_to_finish_with_pass_counts_and_cost():
    api = Api()
    replay = api.create()
    assert replay["status"] == "running" and replay["git_dirty"] is True
    api.upload(replay["id"], api.run(0), api.run(1, score=0.0))
    done = api.finish(replay["id"])
    assert done["status"] == "completed"
    assert done["pass_counts"] == [{"scorer": "steps", "passed": 1, "scored": 2}]
    assert done["cost_usd"] == 8.0  # 1M input tokens at $4/M, twice

    detail = api.call("GET", f"/v1/replays/{replay['id']}", status=200)
    assert [r["run_index"] for r in detail["replay_runs"]] == [0, 1]
    assert [r["scores"][0]["passed"] for r in detail["replay_runs"]] == [True, False]
    assert detail["item"]["rev"] == 2
    assert detail["item"]["contents"]["expected_trajectory"] == [{"tool": "escalate"}]


def test_an_unscored_replay_takes_no_scores():
    api = Api()
    replay = api.create(scored=False)
    assert replay["item_id"] is None and replay["scorers"] == []
    refused = api.upload(replay["id"], api.run(0), status=422)
    assert "unknown scorers: steps" in refused["detail"]["error"]
    api.upload(replay["id"], api.run(0, score=None), api.run(1, score=None))
    assert api.finish(replay["id"])["pass_counts"] == []


def test_a_scored_replay_must_name_its_item_and_scorers_together():
    api = Api()
    api.call("POST", "/v1/replays", api.new(scorers=[]), status=422)
    api.call("POST", "/v1/replays", api.new(item_rev=None), status=422)


def test_a_replay_is_scored_only_against_the_item_made_from_its_trace():
    api = Api()
    wrong = {"trace_id": UNRELATED, "span_id": UNRELATED[:16]}
    refused = api.call("POST", "/v1/replays", api.new(replay_of=wrong), status=422)
    assert "made from the trace it re-ran" in refused["detail"]["error"]
    api.call("POST", "/v1/replays", api.new(item_rev=9), status=404)
    api.call("POST", "/v1/replays", api.new(), key="other", status=404)  # another project's item


def test_retried_writes_change_nothing():
    api = Api()
    body = api.new()
    first = api.call("POST", "/v1/replays", body, status=201)
    assert api.call("POST", "/v1/replays", body, status=200)["id"] == first["id"]
    api.call("POST", "/v1/replays", body | {"runs": 5}, status=409)
    api.upload(first["id"], api.run(0))
    api.upload(first["id"], api.run(0))
    api.upload(first["id"], api.run(1))
    assert api.finish(first["id"])["runs_uploaded"] == 2
    api.finish(first["id"])  # a retried finish succeeds
    api.finish(first["id"], status="failed", code=409)


def test_completed_needs_every_run_but_failed_does_not():
    api = Api()
    replay = api.create()
    api.upload(replay["id"], api.run(0))
    refused = api.finish(replay["id"], code=422)
    assert "1 of 2 runs uploaded" in refused["detail"]["error"]
    assert api.finish(replay["id"], status="failed")["status"] == "failed"
    api.upload(replay["id"], api.run(1), status=409)


def test_run_indexes_are_checked():
    api = Api()
    replay = api.create()
    api.upload(replay["id"], api.run(2), status=422)
    api.upload(replay["id"], api.run(0), api.run(0), status=422)


def test_replays_are_listed_by_original_trace_newest_first_and_per_project():
    api = Api()
    older, newer = api.create(), api.create(scored=False)
    listed = api.call("GET", "/v1/replays", params={"trace_id": FAILED}, status=200)["replays"]
    assert [r["id"] for r in listed] == [newer["id"], older["id"]]
    assert api.call("GET", "/v1/replays", params={"trace_id": UNRELATED})["replays"] == []
    assert api.call("GET", "/v1/replays", params={"trace_id": FAILED}, key="other")["replays"] == []
    api.call("GET", f"/v1/replays/{older['id']}", key="other", status=404)


def test_deleting_the_draft_item_keeps_the_replay_and_what_it_was_scored_against():
    api = Api()
    replay = api.create()
    api.call("DELETE", f"/v1/datasets/{api.dataset}/items/{api.item}?rev=2", status=204)
    detail = api.call("GET", f"/v1/replays/{replay['id']}", status=200)
    assert (detail["item_id"], detail["item_rev"], detail["item"]) == (api.item, 2, None)


def test_a_replay_of_another_projects_trace_is_not_found():
    api = Api()
    body = api.new(scored=False)
    assert api.call("POST", "/v1/replays", body, key="other", status=404) == {
        "detail": {"error": "trace not found; it may have expired"}
    }
    assert api.call("GET", "/v1/replays", params={"trace_id": FAILED}, key="other")["replays"] == []


def test_an_expired_original_can_be_replayed_again_through_its_replay_runs():
    api = Api()
    api.call("POST", "/v1/replays", api.new(scored=False), status=201)
    del api.traces.inputs[FAILED]  # retention expired the original; its replay runs are newer
    api.call("POST", "/v1/replays", api.new(scored=False), status=201)
    # Another project has no replay of it to vouch for it.
    api.call("POST", "/v1/replays", api.new(scored=False), key="other", status=404)


def test_an_expired_trace_never_replayed_is_not_found():
    api = Api()
    del api.traces.inputs[UNRELATED]
    gone = {"trace_id": UNRELATED, "span_id": UNRELATED[:16]}
    api.call("POST", "/v1/replays", api.new(scored=False, replay_of=gone), status=404)


def test_a_fork_of_a_fork_run_names_the_original_at_the_start_of_the_chain():
    got = Api().call("GET", f"/v1/traces/{FORK_RUN}/input", status=200)
    assert got["replay_of"] == {"trace_id": FAILED, "span_id": FAILED[:16]}


def fork_of(trace_id: str, span_id: str = STEP) -> dict:
    return {"fork_of": {"trace_id": trace_id, "span_id": span_id}}


def test_a_fork_records_its_fork_point_and_tool_mode():
    api = Api()
    replay = api.create(scored=False, **fork_of(REPLAY_RUN), tool_mode="recorded")
    assert replay["replay_of"] == {"trace_id": FAILED, "span_id": FAILED[:16]}
    assert replay["fork_of"] == {"trace_id": REPLAY_RUN, "span_id": STEP}
    assert replay["tool_mode"] == "recorded"
    whole = api.create(scored=False)
    assert (whole["fork_of"], whole["tool_mode"]) == (None, "live")


def test_a_fork_is_listed_under_its_original_and_under_the_run_it_forked():
    api = Api()
    fork = api.create(scored=False, **fork_of(REPLAY_RUN))
    whole = api.create(scored=False)

    def listed(trace_id: str) -> list[str]:
        found = api.call("GET", "/v1/replays", params={"trace_id": trace_id}, status=200)
        return [r["id"] for r in found["replays"]]

    assert listed(FAILED) == [whole["id"], fork["id"]]
    assert listed(REPLAY_RUN) == [fork["id"]]


def test_a_fork_must_name_the_original_its_trace_re_ran():
    api = Api()
    wrong = {"trace_id": UNRELATED, "span_id": UNRELATED[:16]}
    got = api.call(
        "POST",
        "/v1/replays",
        api.new(scored=False, replay_of=wrong, **fork_of(REPLAY_RUN)),
        status=422,
    )
    assert FAILED in got["detail"]["error"]


def test_a_fork_run_still_names_its_original_once_the_run_it_forked_expires():
    api = Api()
    del api.traces.inputs[REPLAY_RUN]  # the original is pinned; the run between expired
    got = api.call("GET", f"/v1/traces/{FORK_RUN}/input", status=200)
    assert got["replay_of"] == {"trace_id": FAILED, "span_id": FAILED[:16]}
    api.create(scored=False, **fork_of(FORK_RUN))


def test_a_fork_of_an_expired_trace_is_not_found():
    api = Api()
    del api.traces.inputs[REPLAY_RUN]
    api.call("POST", "/v1/replays", api.new(scored=False, **fork_of(REPLAY_RUN)), status=404)


def test_a_retried_create_with_another_fork_point_conflicts():
    api = Api()
    body = api.new(scored=False, **fork_of(REPLAY_RUN))
    api.call("POST", "/v1/replays", body, status=201)
    api.call("POST", "/v1/replays", body, status=200)
    api.call("POST", "/v1/replays", body | fork_of(REPLAY_RUN, "8" * 16), status=409)
    api.call("POST", "/v1/replays", body | {"tool_mode": "recorded"}, status=409)


def test_fork_runs_keep_where_they_diverged_and_their_warnings():
    api = Api()
    replay = api.create(scored=False, **fork_of(FAILED, FAILED[:16]))
    warning = {"step": 0, "fields": ["system"]}
    api.upload(
        replay["id"],
        api.run(0, score=None, diverged_at=3, warnings=[warning]),
        api.run(1, score=None),
    )
    runs = api.call("GET", f"/v1/replays/{replay['id']}", status=200)["replay_runs"]
    assert [(r["diverged_at"], r["warnings"]) for r in runs] == [(3, [warning]), (None, [])]
    bad = api.run(0, score=None, warnings=[{"step": 0, "fields": []}])
    api.upload(replay["id"], bad, status=422)
