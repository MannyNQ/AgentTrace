"""Phase 4 in the SDK: max_drop and the judge in eval.yaml, the git a PR run records, the LLM
judge against a scripted client, the comparison report, and the CLI's exit code 3."""

import json
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import anthropic
import httpx2
import pytest

from agenttrace import cli, span, tracing
from agenttrace.eval import config, report, scorers
from agenttrace.eval.client import Client, DatasetVersion, VersionItem
from agenttrace.eval.results import ModelUsage, ToolCall
from agenttrace.eval.runner import Git, git_from_env, score

BASE = {"entry": "pkg.mod:fn", "dataset": "support"}
JUDGE = {"name": "helpful", "type": "llm_judge", "model": "claude-haiku-5-5", "criteria": "Polite."}


def cfg(*scorer_dicts) -> config.EvalConfig:
    return config.parse({**BASE, "scorers": list(scorer_dicts)})


# --- eval.yaml --------------------------------------------------------------------------------


def test_max_drop_is_per_scorer_and_not_hashed():
    plain = cfg({"name": "a", "type": "contains"})
    loose = cfg({"name": "a", "type": "contains", "max_drop": 0.2})
    assert plain.scorers[0].spec().max_drop == 0.05
    assert loose.scorers[0].spec().max_drop == 0.2
    assert "max_drop" not in loose.scorers[0].spec().config
    assert plain.hash() == loose.hash()
    with pytest.raises(config.ConfigError, match="max_drop"):
        cfg({"name": "a", "type": "contains", "max_drop": 2})


def test_the_judge_needs_a_model_and_criteria_and_is_hashed():
    judge = cfg(JUDGE)
    assert judge.scorers[0].spec().config == {
        "model": "claude-haiku-5-5",
        "criteria": "Polite.",
        "include": ["input", "output", "expected_output"],
    }
    assert judge.hash() != cfg({**JUDGE, "model": "claude-sonnet-5-5"}).hash()
    assert judge.hash() != cfg({**JUDGE, "criteria": "Rude."}).hash()
    for bad in ({"model": None}, {"criteria": ""}, {"include": ["input"]}, {"temperature": 0}):
        with pytest.raises(config.ConfigError):
            cfg({k: v for k, v in {**JUDGE, **bad}.items() if v is not None})


# --- Git --------------------------------------------------------------------------------------


def test_a_pull_request_records_its_head_and_base_branch(tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"head": {"sha": "head1"}}}))
    env = {
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_SHA": "mergecommit",
        "GITHUB_HEAD_REF": "fix",
        "GITHUB_BASE_REF": "main",
    }
    assert git_from_env(env) == Git(commit="head1", branch="fix", base_branch="main")
    push = {"GITHUB_EVENT_NAME": "push", "GITHUB_SHA": "c1", "GITHUB_REF_NAME": "main"}
    assert git_from_env(push) == Git(commit="c1", branch="main")


def test_merge_base_is_found_with_git(tmp_path, monkeypatch):
    def git(*args: str) -> str:
        out = subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()

    git("init", "-q", "-b", "main")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "a")
    fork = git("rev-parse", "HEAD")
    git("checkout", "-q", "-b", "fix")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "b")
    git("checkout", "-q", "main")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "c")
    monkeypatch.chdir(tmp_path)
    assert cli.merge_base("fix", "main") == fork
    assert cli.merge_base("fix", "nope") is None


# --- The LLM judge ----------------------------------------------------------------------------


class FakeMessages:
    def __init__(self, reply):
        self.reply, self.requests = reply, []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        with span("anthropic.messages.create", "llm"):  # what auto-instrumentation would record
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply


def response(verdict: str | None, stop: str = "end_turn", text: str | None = None):
    body = text if text is not None else json.dumps({"reason": "it was polite", "verdict": verdict})
    return SimpleNamespace(
        model="claude-haiku-5-5",
        stop_reason=stop,
        usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0),
        content=[SimpleNamespace(type="text", text=body)],
    )


def judge(reply, **over) -> tuple[scorers.LlmJudge, FakeMessages]:
    messages = FakeMessages(reply)
    j = scorers.LlmJudge(
        cfg({**JUDGE, **over}).scorers[0],  # pyright: ignore[reportArgumentType]
        client=SimpleNamespace(messages=messages),
    )
    return j, messages


CASE = scorers.Case(
    input={"question": "hi"},
    expected_output="hello",
    output="Hello there!",
    trajectory=[ToolCall(tool="lookup_order", args={"order_id": "1"})],
)


def test_the_judge_passes_and_fails_with_its_reason_and_usage():
    j, messages = judge(response("pass"))
    result = j.grade(CASE)
    assert (result.score, result.reason) == (1.0, "it was polite")
    assert result.usage == (
        ModelUsage(model="claude-haiku-5-5", input_tokens=100, output_tokens=20),
    )
    req = messages.requests[0]
    assert req["model"] == "claude-haiku-5-5"
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert "tool_choice" not in req  # forced tool use is refused by current models
    prompt = req["messages"][0]["content"]
    assert "<criteria>\nPolite.\n</criteria>" in prompt
    assert "<expected_output>\nhello\n</expected_output>" in prompt
    assert "<trajectory>" not in prompt
    assert judge(response("fail"))[0].grade(CASE).score == 0.0


def test_include_picks_what_the_judge_sees():
    j, messages = judge(response("pass"), include=["trajectory"])
    j.grade(scorers.Case(input="secret", output="out", trajectory=CASE.trajectory))
    prompt = messages.requests[0]["messages"][0]["content"]
    assert "lookup_order" in prompt and "secret" not in prompt


def test_the_judge_skips_rather_than_failing_when_it_cant_answer():
    down = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.invalid"))
    with pytest.raises(scorers.Skipped, match="judge unavailable"):
        judge(down)[0].grade(CASE)
    with pytest.raises(scorers.Skipped, match="refusal") as e:
        judge(response(None, stop="refusal"))[0].grade(CASE)
    assert e.value.usage  # a refused call still cost tokens
    with pytest.raises(scorers.Skipped, match="JSON"):
        judge(response(None, text="not json"))[0].grade(CASE)


def test_judge_calls_stay_out_of_traces():
    seen = []
    remove = tracing.add_span_processor(seen.append)
    try:
        judge(response("pass"))[0].grade(CASE)
        with span("not judged"):
            pass
    finally:
        remove()
    assert [s.name for s in seen] == ["not judged"]


def test_scoring_sums_judge_usage_and_reports_skips():
    ok = judge(response("pass"))[0]
    down = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.invalid"))
    other = judge(down, name="other")[0]
    scored = score([ok, other], CASE, None)
    assert [s.scorer for s in scored.scores] == ["helpful"]
    assert scored.judge_usage == [
        ModelUsage(model="claude-haiku-5-5", input_tokens=100, output_tokens=20)
    ]
    assert scored.skipped and scored.skipped[0].startswith("other: judge unavailable")
    # An errored sample never reaches the judge: it scores 0 like every scorer.
    errored = score([ok], CASE, "ValueError: boom")
    assert errored.scores[0].score == 0 and errored.judge_usage == []


# --- The report -------------------------------------------------------------------------------


def experiment(id_: str, **over) -> dict:
    return {
        "id": id_,
        "dataset_version": 1,
        "item_count": 4,
        "samples": 3,
        "git_commit": "abcdef123",
        **over,
    }


ITEM = str(uuid4())
REGRESSED = {
    "experiment": experiment("cur"),
    "baseline": experiment("base"),
    "baseline_reason": "merge_base",
    "regressions": 1,
    "scorers": [
        {
            "scorer": "steps",
            "items": 4,
            "baseline_rate": 0.75,
            "current_rate": 0.0,
            "change": -0.75,
            "ci_low": -1.0,
            "ci_high": -0.25,
            "max_drop": 0.05,
            "regressed": True,
            "underpowered": True,
        },
        {
            "scorer": "answer",
            "items": 2,
            "baseline_rate": 1.0,
            "current_rate": 1.0,
            "change": 0.0,
            "ci_low": 0.0,
            "ci_high": 0.0,
            "max_drop": 0.05,
            "regressed": False,
            "underpowered": False,
        },
    ],
    "item_regressions": [
        {
            "item_id": ITEM,
            "scorer": "steps",
            "baseline_passed": 3,
            "baseline_scored": 3,
            "current_passed": 0,
            "current_scored": 3,
        }
    ]
    * 12,
    "added_items": [str(uuid4())],
    "removed_items": [],
    "current_stats": {"cost_per_sample": 0.0, "p50_latency_ms": 120.0},
    "baseline_stats": {"cost_per_sample": 0.0123, "p50_latency_ms": 3200.0},
}


def test_the_pr_comment_shows_the_verdict_table_and_item_regressions():
    md = report.markdown(
        {}, REGRESSED, "support", {ITEM: {"question": "Where is 1042?"}}, "http://w/"
    )
    assert md.startswith("### ❌ AgentTrace eval: 1 regression\n")
    assert "[baseline](http://w/experiments/base) (at the merge-base `abcdef1`)" in md
    assert "| `steps` | 75% | 0% | -75 pts (-100 pts to -25 pts) | 5 pts | 4 | ❌ regressed |" in md
    assert "| `answer` | 100% | 100% | +0 pts (+0 pts to +0 pts) | 5 pts | 2 | ✅ ok |" in md
    assert "⚠️ Underpowered" in md
    assert "$0.0123 → $0.0000" in md and "3.2 s → 0.1 s" in md
    link = f"[Where is 1042?](http://w/experiments/cur/compare/base/items/{ITEM})"
    assert md.count(link) == report.MAX_ITEM_REGRESSIONS
    assert "- +2 more" in md
    assert "1 items added, 0 removed" in md
    assert "regressed" in report.text(REGRESSED) and "1 regression" in report.text(REGRESSED)


def test_without_a_baseline_the_comment_lists_scores_and_passes():
    cmp = {"experiment": experiment("cur"), "baseline": None, "regressions": 0}
    detail = {"scorer_stats": [{"scorer": "steps", "pass_rate": 0.75, "passed": 9, "scored": 12}]}
    md = report.markdown(detail, cmp, "support", {})
    assert md.startswith("### ⚪ AgentTrace eval: no baseline")
    assert "| `steps` | 75% | 9/12 |" in md
    assert "this run `cur`" in md  # no web URL: IDs instead of links


# --- The CLI gate -----------------------------------------------------------------------------


@pytest.fixture
def fake_server(monkeypatch, tmp_path):
    """The CLI against stubbed API calls and an agent module in tmp_path."""
    (tmp_path / "gate_agent.py").write_text("def entry(input):\n    return 'ok'\n")
    dataset_id = str(uuid4())
    item = VersionItem(str(uuid4()), 1, {"input": {"question": "q"}, "expected_output": "ok"})
    calls = {"comparison": dict(REGRESSED, item_regressions=[]), "created": []}
    monkeypatch.setattr(
        Client, "dataset_version", lambda self, d, v: DatasetVersion(dataset_id, d, 1, [item])
    )
    monkeypatch.setattr(Client, "create_experiment", lambda self, e: calls["created"].append(e))
    monkeypatch.setattr(Client, "upload_samples", lambda self, i, s: None)
    monkeypatch.setattr(Client, "finish", lambda self, i, f: {})
    detail = {
        "id": "cur", "status": "completed", "samples_uploaded": 1, "item_count": 1,
        "cost_usd": 0.0, "unpriced_samples": 0, "items": [],
        "scorer_stats": [{"scorer": "a", "passed": 1, "scored": 1, "pass_rate_by_sample": [1.0]}],
    }  # fmt: skip
    monkeypatch.setattr(Client, "experiment", lambda self, i: detail)
    monkeypatch.setattr(Client, "comparison", lambda self, i: calls["comparison"])
    monkeypatch.setenv("AGENTTRACE_API_KEY", "k")
    monkeypatch.setenv("GITHUB_SHA", "c1")
    monkeypatch.setattr(sys, "path", list(sys.path))
    (tmp_path / "eval.yaml").write_text(
        "entry: gate_agent:entry\ndataset: support\nsamples: 1\n"
        "scorers: [{name: a, type: contains}]\n"
    )
    return calls


def gate(tmp_path, *flags: str) -> int:
    with pytest.raises(SystemExit) as e:
        cli.main(["eval", "-c", str(tmp_path / "eval.yaml"), *flags])
    return int(e.value.code or 0)


def test_a_regression_exits_3_only_with_the_flag(fake_server, tmp_path, capsys):
    assert gate(tmp_path) == 0
    assert "1 regression" in capsys.readouterr().out
    md = tmp_path / "report.md"
    assert gate(tmp_path, "--fail-on-regression", "--report-md", str(md)) == 3
    assert md.read_text().startswith("### ❌ AgentTrace eval: 1 regression")


def test_no_baseline_or_no_regression_passes_the_gate(fake_server, tmp_path):
    fake_server["comparison"] = dict(REGRESSED, regressions=0)
    assert gate(tmp_path, "--fail-on-regression") == 0
    fake_server["comparison"] = {**REGRESSED, "baseline": None, "regressions": 0}
    assert gate(tmp_path, "--fail-on-regression") == 0


def test_base_records_the_branch_even_without_a_merge_base(fake_server, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # not a git repo: no merge-base to find
    assert gate(tmp_path, "--base", "main") == 0
    created = fake_server["created"][-1]
    assert (created.git_base_branch, created.git_merge_base) == ("main", None)
