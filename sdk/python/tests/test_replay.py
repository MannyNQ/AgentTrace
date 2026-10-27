"""`agenttrace replay` against a scripted agent and stubbed API calls (ADR-0028, ADR-0030)."""

import sys
from collections import defaultdict
from uuid import uuid4

import pytest

from agenttrace import cli
from agenttrace.eval.client import ApiError, Client, VersionItem
from agenttrace.eval.replay import (
    REPLAY_OF_ATTRIBUTE,
    REPLAY_ORIGIN_ATTRIBUTE,
    REPLAY_SPAN,
    REPLAY_TAG,
)

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
        self.steps: list[dict] = []
        for name in (
            "trace_steps",
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

    def trace_steps(self, trace_id: str) -> list[dict]:
        assert trace_id == FAILED  # the trace named, whose steps are played back
        return self.steps

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


def test_require_pass_fails_when_every_scorer_skips_every_run(api, tmp_path, capsys):
    (tmp_path / "skipper.py").write_text("def never(case):\n    return None\n")
    (tmp_path / "eval.yaml").write_text(
        "entry: replay_agent:entry\ndataset: support\nsamples: 2\n"
        "scorers: [{name: judge, type: custom, function: 'skipper:never'}]\n"
    )
    agent(tmp_path, broken=True)
    assert replay(FAILED) == 0
    assert "no run was graded" in capsys.readouterr().out
    assert replay(FAILED, "--require-pass") == 3


# --- Forks and tool stubbing (ADR-0040) ------------------------------------------------------

FORK_AGENT = """
import os
from pathlib import Path

import anthropic
from agenttrace import RecordedToolError, call_tool, instrument_anthropic

instrument_anthropic()
SYSTEM = {system!r}
ORDER = {order!r}


def lookup(order):
    Path("tool-ran").write_text(order)  # a side effect the stub must not repeat
    return {{"order": order, "status": "shipped"}}


def entry(input):
    client = anthropic.Anthropic(api_key="k", base_url=os.environ["FORK_LLM"], max_retries=0)
    ask = dict(model="claude-opus-5-5", max_tokens=10, system=SYSTEM)
    messages = [{{"role": "user", "content": input["question"]}}]
    first = client.messages.create(**ask, messages=messages)
    try:
        result = call_tool("lookup", {{"order": ORDER}}, lookup)
    except RecordedToolError as e:
        result = str(e)
    messages += [
        {{"role": "assistant", "content": first.content}},
        {{"role": "user", "content": str(result)}},
    ]
    return client.messages.create(**ask, messages=messages).content[0].text
"""
LLM_1, TOOL_1, LLM_2 = "1" * 16, "2" * 16, "3" * 16


def message(text: str | None = None, tool_use: bool = False) -> dict:
    content = (
        [{"type": "tool_use", "id": "t1", "name": "lookup", "input": {"order": "42"}}]
        if tool_use
        else [{"type": "text", "text": text}]
    )
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": content, "stop_reason": "tool_use" if tool_use else "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 2},
    }  # fmt: skip


def step(index: int, span_id: str, kind: str, **over) -> dict:
    return {
        "index": index, "span_id": span_id, "parent_span_id": ROOT, "kind": kind,
        "name": "anthropic.messages.create" if kind == "llm" else "lookup", "status": "ok",
        "error_type": "", "error_message": None,
        "interceptor": "anthropic" if kind == "llm" else "call_tool", "truncated": [],
        "output_recorded": None if kind == "llm" else "json", "input": None, "output": None,
    } | over  # fmt: skip


def recorded_steps(system: str = "be brief") -> list[dict]:
    ask = {"model": "claude-opus-5-5", "max_tokens": 10, "system": system}
    first = {**ask, "messages": [{"role": "user", "content": INPUT["question"]}]}
    looked_up = {"order": "42", "status": "recorded"}
    return [
        step(0, LLM_1, "llm", input=first, output=message(tool_use=True)),
        step(1, TOOL_1, "tool", input={"order": "42"}, output=looked_up),
        step(2, LLM_2, "llm", input=ask, output=message("it shipped")),
    ]


@pytest.fixture
def fork_api(api, tmp_path, monkeypatch, http_server):
    """The original: an LLM call asking for `lookup`, the tool call, and an LLM call answering.
    The live model answers with the text `live`, and `calls` counts what reached it."""
    (tmp_path / "eval.yaml").write_text(EVAL_YAML.replace("replay_agent", "fork_agent"))
    fork_agent(tmp_path)
    api.calls = []

    def answer(path, headers, body):
        api.calls.append(body)
        return 200, message("live")

    monkeypatch.setenv("FORK_LLM", http_server(answer))
    api.steps = recorded_steps()
    yield api
    sys.modules.pop("fork_agent", None)


def fork_agent(tmp_path, system: str = "be brief", order: str = "42") -> None:
    (tmp_path / "fork_agent.py").write_text(FORK_AGENT.format(system=system, order=order))
    sys.modules.pop("fork_agent", None)


def test_a_fork_plays_the_prefix_back_and_runs_live_from_the_fork_point(
    fork_api, tmp_path, collected, capsys
):
    assert replay(FAILED, "--from", LLM_2[:8], "--samples", "1") == 0
    assert len(fork_api.calls) == 1  # only the fork point reached the model
    assert not (tmp_path / "tool-ran").exists()  # the prefix's tool was answered, not run
    created = fork_api.created[0]
    assert created.fork_of.trace_id == FAILED and created.fork_of.span_id == LLM_2
    assert created.replay_of.span_id == ROOT and created.tool_mode == "live"
    [run] = fork_api.runs
    assert run.output == "live" and run.diverged_at is None and run.warnings == []
    assert run.trajectory[0].tool == "lookup"  # the stubbed call is still a step of the run
    root = next(s for s in collected.spans if s.name == REPLAY_SPAN)
    assert root.attributes[REPLAY_OF_ATTRIBUTE] == f"{FAILED}:{LLM_2}"
    assert root.attributes[REPLAY_ORIGIN_ATTRIBUTE] == f"{FAILED}:{ROOT}"
    stubbed = [s for s in collected.spans if s.attributes.get("agenttrace.stubbed") == "true"]
    assert [s.name for s in stubbed] == ["anthropic.messages.create", "lookup"]
    assert (stubbed[0].input_tokens, stubbed[0].output_tokens) == (0, 0)  # nothing was billed
    assert stubbed[1].output == {"order": "42", "status": "recorded"}
    assert "at step 2 (llm anthropic.messages.create" in capsys.readouterr().err


def test_a_changed_prompt_still_gets_the_stub_and_a_warning(fork_api, tmp_path, capsys):
    fork_agent(tmp_path, system="be brief and kind")
    assert replay(FAILED, "--from", LLM_2, "--samples", "1") == 0
    assert len(fork_api.calls) == 1
    [run] = fork_api.runs
    assert [(w.step, w.fields) for w in run.warnings] == [(0, ["system"])]
    assert "1 prefix requests differ from the recording: steps 0" in capsys.readouterr().err


def test_a_prefix_tool_call_the_recording_never_made_goes_live_and_says_where(
    fork_api, tmp_path, capsys
):
    fork_agent(tmp_path, order="43")
    assert replay(FAILED, "--from", LLM_2, "--samples", "1") == 0
    assert (tmp_path / "tool-ran").read_text() == "43"
    [run] = fork_api.runs
    assert run.diverged_at == 1
    assert "left the recording at step 1" in capsys.readouterr().err


def test_forking_at_a_tool_runs_that_tool_live(fork_api, tmp_path):
    assert replay(FAILED, "--from", TOOL_1, "--samples", "1") == 0
    assert (tmp_path / "tool-ran").exists()
    assert len(fork_api.calls) == 1  # the reply asking for the tool was stubbed
    assert fork_api.runs[0].diverged_at is None


def test_stub_tools_runs_the_model_live_and_no_tool(fork_api, tmp_path):
    assert replay(FAILED, "--stub-tools", "--samples", "1") == 0
    assert len(fork_api.calls) == 2 and not (tmp_path / "tool-ran").exists()
    created = fork_api.created[0]
    assert created.fork_of is None and created.tool_mode == "recorded"


def test_stub_tools_errors_a_run_whose_call_has_no_recording(fork_api, tmp_path):
    fork_agent(tmp_path, order="43")
    assert replay(FAILED, "--stub-tools", "--samples", "1") == 0
    assert not (tmp_path / "tool-ran").exists()
    [run] = fork_api.runs
    assert run.error is not None and run.error.startswith("NoRecording: ")


def test_a_recorded_tool_error_is_raised_again(fork_api, tmp_path, collected):
    fork_api.steps[1] |= {
        "status": "error", "error_type": "ToolError", "error_message": "no order 42",
        "output_recorded": None, "output": None,
    }  # fmt: skip
    assert replay(FAILED, "--from", LLM_2, "--samples", "1") == 0
    tool = next(s for s in collected.spans if s.name == "lookup")
    assert (tool.error_type, tool.attributes["agenttrace.error_message"]) == (
        "ToolError",
        "no order 42",
    )
    assert "no order 42" in fork_api.calls[0].decode()  # the agent saw the recorded error


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({1: {"interceptor": None}}, f"step 1 (tool lookup, span {TOOL_1}) can't be played back"),
        ({1: {"interceptor": None}}, "it wasn't recorded by call_tool()"),
        ({1: {"output_recorded": "lossy"}}, "its output didn't survive recording"),
        ({1: {"output_recorded": None}}, "may predate Phase 9's SDK"),
        ({0: {"truncated": ["input"]}}, "the SDK cut its input"),
        ({0: {"status": "error"}}, "a failed LLM call isn't recorded in full"),
        ({0: {"interceptor": None}}, "wasn't recorded by instrument_anthropic()"),
        ({2: {"parent_span_id": TOOL_1}}, "runs inside tool step 1"),
    ],
)  # fmt: skip
def test_a_fork_the_recording_can_t_stand_in_for_is_refused_naming_the_step(
    fork_api, capsys, change, says
):
    for i, fields in change.items():
        fork_api.steps[i] |= fields
    target = LLM_2 if 2 not in change else "4" * 16
    if 2 in change:
        fork_api.steps.append(step(3, target, "llm", input={}, output=message("x")))
    assert replay(FAILED, "--from", target) == 2
    assert says in capsys.readouterr().err
    assert fork_api.created == []


def test_a_fork_point_must_be_a_step(fork_api, capsys):
    assert replay(FAILED, "--from", "f" * 16) == 2
    assert "isn't an LLM or tool span of trace" in capsys.readouterr().err
    fork_api.steps[2]["interceptor"] = None
    assert replay(FAILED, "--from", LLM_2) == 2
    assert "can't tell when the run gets there" in capsys.readouterr().err
