"""`agenttrace replay` against a scripted agent and stubbed API calls (ADR-0028, ADR-0030)."""

import sys
from collections import defaultdict
from uuid import uuid4

import pytest

from agenttrace import cli
from agenttrace.eval.client import ApiError, Client, VersionItem
from agenttrace.eval.replay import REPLAY_OF_ATTRIBUTE, REPLAY_SPAN, REPLAY_TAG

FAILED = "a" * 32  # the production trace that went wrong
REPLAY_RUN = "b" * 32  # an earlier replay run of FAILED
ROOT = FAILED[:16]
INPUT = {"question": "my keyboard broke, refund it"}
AGENT = """
from agenttrace import span

BROKEN = {broken}

def entry(input):
    tool = "issue_refund" if BROKEN else "escalate"
    with span(tool, "tool", input={{"order": 42}}):
        pass
    return "done"
"""
EVAL_YAML = (
    "entry: replay_agent:entry\ndataset: support\nsamples: 2\n"
    "scorers: [{name: steps, type: trajectory, mode: exact}]\n"
)


class FakeApi:
    """The calls `agenttrace replay` makes, answered like ingest would."""

    def __init__(self, monkeypatch):
        self.item: VersionItem | None = VersionItem(
            str(uuid4()), 2, {"input": INPUT, "expected_trajectory": [{"tool": "escalate"}]}
        )
        self.input_error: ApiError | None = None
        self.created: list = []
        self.runs: list = []
        self.finished: list = []
        for name in (
            "dataset",
            "draft_item_for_trace",
            "trace_input",
            "create_replay",
            "upload_replay_runs",
            "finish_replay",
            "replay",
        ):
            fake = getattr(self, name)
            monkeypatch.setattr(Client, name, lambda _, *a, f=fake: f(*a))

    def dataset(self, name: str) -> dict:
        return {"id": "d1", "name": name}

    def draft_item_for_trace(self, dataset_id: str, trace_id: str) -> VersionItem | None:
        assert trace_id == FAILED  # always looked up by the original, never a replay run
        return self.item

    def trace_input(self, trace_id: str) -> dict:
        if self.input_error:
            raise self.input_error
        replay_of = {"trace_id": FAILED, "span_id": ROOT} if trace_id == REPLAY_RUN else None
        return {
            "trace_id": trace_id,
            "span_id": trace_id[:16],
            "input": INPUT,
            "replay_of": replay_of,
        }

    def create_replay(self, r) -> dict:
        self.created.append(r)
        return {}

    def upload_replay_runs(self, replay_id: str, runs: list) -> None:
        self.runs += runs

    def finish_replay(self, replay_id: str, f) -> dict:
        self.finished.append(f)
        return {}

    def replay(self, replay_id: str) -> dict:
        r = self.created[-1]
        counts = defaultdict(lambda: [0, 0])
        for run in self.runs:
            for sc in run.scores:
                counts[sc.scorer][0] += sc.score >= 1.0
                counts[sc.scorer][1] += 1
        return {
            "id": replay_id,
            "status": "completed",
            "runs_uploaded": len(self.runs),
            "errored": sum(run.error is not None for run in self.runs),
            "cost_usd": 0.0,
            "unpriced_runs": 0,
            "item_id": None if r.item_id is None else str(r.item_id),
            "item_rev": r.item_rev,
            "pass_counts": [
                {"scorer": s, "passed": p, "scored": n} for s, (p, n) in counts.items()
            ],
        }


@pytest.fixture
def api(monkeypatch, tmp_path, collected):
    monkeypatch.setenv("AGENTTRACE_API_KEY", "k")
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("replay_agent", None)
    agent(tmp_path, broken=False)
    (tmp_path / "eval.yaml").write_text(EVAL_YAML)
    yield FakeApi(monkeypatch)
    sys.modules.pop("replay_agent", None)


def agent(tmp_path, broken: bool) -> None:
    (tmp_path / "replay_agent.py").write_text(AGENT.format(broken=broken))
    sys.modules.pop("replay_agent", None)


def replay(*args: str) -> int:
    with pytest.raises(SystemExit) as e:
        cli.main(["replay", *args])
    return int(e.value.code or 0)


def test_a_fixed_agent_passes_every_run_against_the_draft_item(api, collected, capsys):
    assert replay(FAILED, "--require-pass", "--web-url", "http://web") == 0
    created = api.created[0]
    assert created.replay_of.trace_id == FAILED and created.replay_of.span_id == ROOT
    assert (str(created.item_id), created.item_rev) == (api.item.item_id, 2)
    assert [s.name for s in created.scorers] == ["steps"]
    assert created.runs == 2 and created.config_hash is not None
    assert sorted(r.run_index for r in api.runs) == [0, 1]
    assert all(r.scores[0].score == 1.0 for r in api.runs)
    assert api.finished[0].status == "completed"
    out = capsys.readouterr().out
    assert "steps  2/2  pass" in out
    assert f"http://web/replays/{created.id}" in out


def test_each_run_is_its_own_trace_pointing_at_the_original_root_span(api, collected):
    replay(FAILED)
    roots = [s for s in collected.spans if s.name == REPLAY_SPAN]
    assert len({s.trace_id for s in roots}) == 2
    for s in roots:
        assert s.attributes[REPLAY_OF_ATTRIBUTE] == f"{FAILED}:{ROOT}"
        assert s.input == INPUT
        assert s.trace is not None and REPLAY_TAG in s.trace.tags
    assert {r.trace_id for r in api.runs} == {s.trace_id for s in roots}


def test_a_still_broken_agent_exits_3_only_with_require_pass(api, tmp_path, capsys):
    agent(tmp_path, broken=True)
    assert replay(FAILED) == 0
    assert "steps  0/2  FAIL" in capsys.readouterr().out
    assert replay(FAILED, "--require-pass") == 3


def test_replaying_a_replay_run_replays_its_original(api, capsys):
    assert replay(REPLAY_RUN) == 0
    assert api.created[0].replay_of.trace_id == FAILED
    assert "is a replay run; replaying its original" in capsys.readouterr().err


def test_without_an_expectation_the_replay_runs_unscored(api, capsys):
    api.item = VersionItem(api.item.item_id, 1, {"input": INPUT})
    assert replay(FAILED) == 0
    assert api.created[0].item_id is None and api.created[0].scorers == []
    assert all(r.scores == [] for r in api.runs) and len(api.runs) == 2
    assert "unscored: item" in capsys.readouterr().err


@pytest.mark.parametrize("item", [None, "no expectation"])
def test_require_pass_with_nothing_to_score_exits_2_before_running(api, capsys, item):
    api.item = None if item is None else VersionItem(str(uuid4()), 1, {"input": INPUT})
    assert replay(FAILED, "--require-pass") == 2
    assert api.created == [] and api.runs == []
    assert "--require-pass needs something to score" in capsys.readouterr().err


def test_entry_alone_runs_unscored_with_the_defaults(api, tmp_path):
    (tmp_path / "eval.yaml").unlink()
    assert replay(FAILED, "--entry", "replay_agent:entry") == 0
    created = api.created[0]
    assert created.runs == 3 and created.item_id is None and created.config is None
    assert replay(FAILED, "--entry", "replay_agent:entry", "--require-pass") == 2


def test_problems_before_the_run_exit_2_and_record_nothing(api, tmp_path, capsys):
    api.input_error = ApiError("GET /v1/traces/x/input: 422 the SDK cut this trace's input", 422)
    assert replay(FAILED) == 2
    assert "cut this trace's input" in capsys.readouterr().err
    api.input_error = None
    assert replay(FAILED, "--samples", "0") == 2
    assert replay(FAILED, "--entry", "replay_agent:nothing") == 2
    (tmp_path / "eval.yaml").unlink()
    assert replay(FAILED) == 2
    assert "pass --entry" in capsys.readouterr().err
    assert api.created == []
