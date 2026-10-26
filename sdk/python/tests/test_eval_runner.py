"""The runner against a scripted agent and an in-memory API, so no server or model is needed."""

import asyncio
import random
import threading
import time
from uuid import uuid4

import pytest

from agenttrace import span, tracing
from agenttrace.eval import config, scorers
from agenttrace.eval.client import ApiError, DatasetVersion, VersionItem
from agenttrace.eval.results import Finish, NewExperiment, SampleResult
from agenttrace.eval.runner import Git, RunError, Runner, git_from_env, storable_output


class FakeApi:
    def __init__(self, fail_upload_after: int | None = None):
        self.created: list[NewExperiment] = []
        self.batches: list[list[SampleResult]] = []
        self.finished: list[Finish] = []
        self.fail_upload_after = fail_upload_after

    def create_experiment(self, e: NewExperiment) -> dict:
        self.created.append(e)
        return {}

    def upload_samples(self, experiment_id: str, samples: list[SampleResult]) -> None:
        if self.fail_upload_after is not None and len(self.batches) >= self.fail_upload_after:
            raise ApiError("POST samples: couldn't reach the server")
        self.batches.append(list(samples))

    def finish(self, experiment_id: str, finish: Finish) -> dict:
        self.finished.append(finish)
        return {}

    def experiment(self, experiment_id: str) -> dict:
        return {"id": experiment_id}

    @property
    def samples(self) -> list[SampleResult]:
        return [s for b in self.batches for s in b]


def item(question: str, **contents) -> VersionItem:
    return VersionItem(str(uuid4()), 1, {"input": {"question": question}, **contents})


SUPPORT = DatasetVersion(
    dataset_id=str(uuid4()),
    name="support",
    version=2,
    items=[
        item("where is 1042", expected_output="UPS",
             expected_trajectory=[{"tool": "lookup_order", "args": {"order_id": "1042"}}]),
        item("refund 1043",
             expected_trajectory=[{"tool": "lookup_order"}, {"tool": "issue_refund"}]),
        item("crash"),
        item("hang"),
    ],
)  # fmt: skip


def support_agent(input: dict) -> str:
    """Calls tools as spans with jitter, so concurrent samples interleave."""
    q = input["question"]
    with span("support_agent", "agent", input=input):
        if q == "crash":
            raise ValueError("model declined")
        if q == "hang":
            time.sleep(2)
            return "late"
        order = q.split()[-1]
        time.sleep(random.uniform(0, 0.02))
        with span("lookup_order", "tool", input={"order_id": order}):
            time.sleep(random.uniform(0, 0.02))
        with span("anthropic.messages.create", "llm") as llm:
            llm.set_llm("claude-haiku-5-5", input_tokens=100, output_tokens=10)
        if q.startswith("refund"):
            with span("issue_refund", "tool", input={"order_id": order, "reason": "broken"}):
                pass
        return f"Order {order} ships with UPS"


EVAL = {
    "entry": "tests:support_agent",
    "dataset": "support",
    "samples": 3,
    "concurrency": 4,
    "timeout_s": 0.5,
    "scorers": [
        {"name": "answer", "type": "contains"},
        {"name": "steps", "type": "trajectory", "mode": "in_order"},
        {"name": "short", "type": "trajectory", "mode": "max_steps", "max": 1},
    ],
}


@pytest.fixture(autouse=True)
def no_exporter(monkeypatch):
    """Spans reach the runner through its processor even when nothing is exported."""
    monkeypatch.setattr(tracing, "get_exporter", lambda: None)


def run(entry=support_agent, api=None, version=SUPPORT, **over):
    cfg = config.parse({**EVAL, **over})
    api = api or FakeApi()
    lines: list[str] = []
    runner = Runner(cfg, api, entry, scorers.build(cfg), report=lines.append)  # type: ignore[arg-type]
    result = runner.run(version, Git(commit="abc", branch="main"))
    return api, result, lines


def by_item(api: FakeApi) -> dict[str, list[SampleResult]]:
    out: dict[str, list[SampleResult]] = {}
    for s in sorted(api.samples, key=lambda s: s.sample_index):
        out.setdefault(str(s.item_id), []).append(s)
    return out


def test_every_sample_is_run_scored_uploaded_and_finished():
    api, result, lines = run()
    [created] = api.created
    assert (created.dataset_version, created.samples, created.git_commit) == (2, 3, "abc")
    assert [s.name for s in created.scorers] == ["answer", "steps", "short"]
    assert created.config_hash == config.parse(EVAL).hash()
    assert len(api.samples) == 12 and len(lines) == 12
    assert {(str(s.item_id), s.sample_index) for s in api.samples} == {
        (i.item_id, n) for i in SUPPORT.items for n in range(3)
    }
    assert api.finished == [Finish(status="completed")]
    assert result.abandoned  # the "hang" item's threads were left running
    assert len({s.trace_id for s in api.samples}) == 12  # one trace per sample


def test_trajectories_stay_with_their_own_sample_under_concurrency():
    api, _, _ = run(concurrency=8, samples=5)
    where, refund = (by_item(api)[i.item_id] for i in SUPPORT.items[:2])
    for s in where:
        assert [(c.tool, c.args) for c in s.trajectory] == [("lookup_order", {"order_id": "1042"})]
    for s in refund:
        assert [c.tool for c in s.trajectory] == ["lookup_order", "issue_refund"]
        assert s.usage[0].model_dump() == {
            "model": "claude-haiku-5-5",
            "input_tokens": 100,
            "output_tokens": 10,
            "cache_read_tokens": 0,
            "cache_write_5m_tokens": 0,
            "cache_write_1h_tokens": 0,
        }


def test_scorers_skip_items_without_their_expectation():
    api, _, _ = run()
    where, refund, crash, _ = (by_item(api)[i.item_id] for i in SUPPORT.items)
    assert {sc.scorer: sc.score for sc in where[0].scores} == {"answer": 1, "steps": 1, "short": 1}
    # No expected_output, so `answer` is skipped; two tool calls break `short`.
    assert {sc.scorer: sc.score for sc in refund[0].scores} == {"steps": 1, "short": 0}
    assert [sc.reason for sc in refund[0].scores if sc.scorer == "short"] == [
        "2 tool calls; the limit is 1"
    ]
    # An errored sample: only the scorers that apply to the item, each 0.
    assert [(sc.scorer, sc.score, sc.reason) for sc in crash[0].scores] == [
        ("short", 0, "agent raised ValueError")
    ]


def test_errors_and_timeouts_are_errored_samples():
    api, _, lines = run()
    crash, hang = (by_item(api)[i.item_id] for i in SUPPORT.items[2:])
    assert {s.error for s in crash} == {"ValueError: model declined"}
    assert {s.error for s in hang} == {"TimeoutError: no result after 0.5 s"}
    assert all(s.output is None for s in crash + hang)
    assert any("error (ValueError: model declined)" in line for line in lines)


def test_async_entry_points_are_cancelled_on_timeout():
    cancelled = threading.Event()

    async def agent(input: dict) -> str:
        with span("lookup_order", "tool", input={"order_id": "1"}):
            pass
        if input["question"] == "hang":
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled.set()
                raise
        return "UPS"

    version = DatasetVersion(
        SUPPORT.dataset_id, "support", 2, [item("1", expected_output="UPS"), item("hang")]
    )
    api, result, _ = run(entry=agent, version=version, samples=1)
    ok, hang = (by_item(api)[i.item_id][0] for i in version.items)
    assert ok.output == "UPS" and [c.tool for c in ok.trajectory] == ["lookup_order"]
    assert hang.error and hang.error.startswith("TimeoutError")
    assert cancelled.is_set() and not result.abandoned


def test_uploads_are_batched():
    many = DatasetVersion(
        SUPPORT.dataset_id, "support", 2, [item(f"where is {n}") for n in range(15)]
    )
    api, _, _ = run(version=many, samples=3, concurrency=16)
    assert [len(b) for b in api.batches] == [20, 20, 5]


def test_a_run_that_stops_finishes_the_experiment_failed():
    many = DatasetVersion(
        SUPPORT.dataset_id, "support", 2, [item(f"where is {n}") for n in range(15)]
    )
    api = FakeApi(fail_upload_after=1)
    with pytest.raises(RunError, match="couldn't reach"):
        run(version=many, api=api, samples=3)
    [finish] = api.finished
    assert finish.status == "failed" and "couldn't reach" in (finish.error or "")


def test_outputs_are_made_storable():
    assert storable_output({"score": float("nan")}) == ('{"score": NaN}', False)
    assert storable_output("bad\x00byte") == ("bad�byte", False)
    value, cut = storable_output("x" * (2 * 1024 * 1024))
    assert cut and isinstance(value, str) and len(value.encode()) <= 1024 * 1024


def test_git_info_comes_from_github_actions():
    env = {"GITHUB_SHA": "f00", "GITHUB_HEAD_REF": "fix-refunds", "GITHUB_REF_NAME": "12/merge"}
    assert git_from_env(env) == Git(commit="f00", branch="fix-refunds")
    assert git_from_env({"GITHUB_REF_NAME": "main"}) == Git(branch="main")
    assert git_from_env({}) == Git()
