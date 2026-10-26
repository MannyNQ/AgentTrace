"""`agenttrace replay` (ADR-0028): run the entry point again on a recorded trace's root input,
`runs` times, through the eval runner, and stream the replay runs to `/v1/replays` (ADR-0029).

Each replay run's root span carries `agenttrace.replay_of`, `<trace_id>:<span_id>` of the original
trace's root span. Replaying a replay run points at its original instead, so the link never
chains. A replay is scored only when `eval.yaml`'s dataset has a draft item made from the original
trace and that item has an expectation; it's then scored against that draft revision (ADR-0030).
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agenttrace.eval import scorers as sc
from agenttrace.eval.client import ApiError, Client, VersionItem
from agenttrace.eval.config import EvalConfig
from agenttrace.eval.results import (
    Finish,
    NewReplay,
    ReplayRunResult,
    ScorerSpec,
    SpanRef,
    ToolCall,
)
from agenttrace.eval.runner import (
    MAX_ERROR,
    AgentRunner,
    Git,
    RunResult,
    StartError,
    _clean,
    _Sample,
)

REPLAY_SPAN = "agenttrace.replay.run"
REPLAY_TAG = "agenttrace-replay"
REPLAY_OF_ATTRIBUTE = "agenttrace.replay_of"  # services/ingest/.../trace_inputs.py reads it
# Without eval.yaml (`--entry` alone): GLOSSARY.md, design.md Phase 5.
DEFAULT_RUNS = 3
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_CONCURRENCY = 1


@dataclass(frozen=True)
class Original:
    """The trace a replay re-runs: its root span and full input."""

    ref: SpanRef
    input: Any


def original(found: dict[str, Any]) -> Original:
    """`GET /v1/traces/{id}/input`'s answer, resolved to the original when the trace is itself a
    replay run. Both have the same input, since the run was called with it."""
    ref = found["replay_of"] or {"trace_id": found["trace_id"], "span_id": found["span_id"]}
    return Original(SpanRef(**ref), found["input"])


@dataclass(frozen=True)
class Scoring:
    """What a replay is scored against, or why it isn't."""

    item: VersionItem | None
    scorers: list[sc.Scorer]
    specs: list[ScorerSpec]
    unscored_reason: str | None = None


def unscored(reason: str) -> Scoring:
    return Scoring(None, [], [], reason)


def _has_expectation(item: VersionItem) -> bool:
    c = item.contents
    return c.get("expected_output") is not None or c.get("expected_trajectory") is not None


def scoring(
    cfg: EvalConfig | None, client: Client, o: Original, build: Callable[[], list[sc.Scorer]]
) -> Scoring:
    """Finds the draft item made from the original trace in `eval.yaml`'s dataset. Scorers are
    built only for a scored replay, so an unscored one never needs a judge's credentials. Raises
    ApiError or ScorerError."""
    if cfg is None:
        return unscored("no eval.yaml, so no dataset to find the trace's item in")
    dataset = client.dataset(cfg.dataset)
    draft = client.draft_item_for_trace(dataset["id"], o.ref.trace_id)
    if draft is None:
        return unscored(f"the trace isn't in dataset \"{dataset['name']}\"'s draft")
    if not _has_expectation(draft):
        return unscored(f"item {draft.item_id[:8]} has no expected output or trajectory yet")
    item = VersionItem(draft.item_id, draft.rev, {**draft.contents, "input": o.input})
    scorers = build()
    expected = item.contents.get("expected_trajectory")
    case = sc.Case(
        input=o.input,
        expected_output=item.contents.get("expected_output"),
        expected_trajectory=None if expected is None else [ToolCall(**s) for s in expected],
    )
    if not any(s.applies(case) for s in scorers):
        return unscored(f"no scorer in eval.yaml applies to item {draft.item_id[:8]}")
    return Scoring(item, scorers, [s.spec() for s in cfg.scorers])


class ReplayRunner(AgentRunner):
    root_span = REPLAY_SPAN
    tag = REPLAY_TAG

    def __init__(
        self,
        client: Client,
        entry: Callable[[Any], Any],
        o: Original,
        how: Scoring,
        thresholds: dict[str, float],
        concurrency: int,
        timeout_s: float,
        report: Callable[[str], None] = print,
    ):
        super().__init__(concurrency, timeout_s, entry, how.scorers, thresholds, report)
        self.client, self.original, self.how = client, o, how
        # An unscored replay still runs through an item: the input alone, so no scorer applies.
        self.item = how.item or VersionItem("", 0, {"input": o.input})

    def _metadata(self, run_id: str, item: VersionItem, index: int) -> dict[str, str]:
        return {"replay_id": run_id, "run": str(index)}

    def _attributes(self) -> dict[str, str]:
        ref = self.original.ref
        return {REPLAY_OF_ATTRIBUTE: f"{ref.trace_id}:{ref.span_id}"}

    def _record(self, s: _Sample, fields: dict[str, Any]) -> ReplayRunResult:
        return ReplayRunResult(run_index=s.index, **fields)

    def _upload(self, run_id: str, batch: list[Any]) -> None:
        self.client.upload_replay_runs(run_id, batch)

    def _label(self, s: _Sample) -> str:
        return f"run #{s.index}"

    def run(self, runs: int, git: Git, dirty: bool | None, cfg: EvalConfig | None) -> RunResult:
        """Creates the replay, runs it, and finishes it. Anything that stops the run after the
        replay exists finishes it as `failed` and raises RunError."""
        replay_id = str(uuid.uuid4())
        item = self.how.item
        new = NewReplay(
            id=uuid.UUID(replay_id),
            replay_of=self.original.ref,
            runs=runs,
            item_id=None if item is None else uuid.UUID(item.item_id),
            item_rev=None if item is None else item.rev,
            scorers=self.how.specs,
            config=None if cfg is None else cfg.as_json(),
            config_hash=None if cfg is None else cfg.hash(),
            git_commit=git.commit,
            git_branch=git.branch,
            git_dirty=dirty,
        )
        try:
            self.client.create_replay(new)
        except ApiError as e:
            raise StartError(f"couldn't create the replay: {e}") from e
        jobs = [(self.item, i) for i in range(runs)]
        abandoned = self._execute(replay_id, jobs, lambda e: self._fail(replay_id, e))
        self.client.finish_replay(replay_id, Finish(status="completed"))
        return RunResult(replay_id, self.client.replay(replay_id), abandoned)

    def _fail(self, replay_id: str, error: str) -> None:
        try:
            finish = Finish(status="failed", error=_clean(error)[:MAX_ERROR])
            self.client.finish_replay(replay_id, finish)
        except Exception as e:  # the server may be why the run failed
            self.report(f"could not mark replay {replay_id} failed: {e}")


def all_pass(detail: dict[str, Any]) -> bool:
    """Every scored run passed every scorer that graded it. An errored run scores 0, so it
    fails. A replay where every scorer skipped every run (a custom scorer returning None, the
    judge unreachable) graded nothing, so it doesn't pass: a gate mustn't pass on nothing."""
    counts = detail["pass_counts"]
    return bool(counts) and all(c["passed"] == c["scored"] for c in counts)


def summary(detail: dict[str, Any], web_url: str | None) -> str:
    cost = f"${detail['cost_usd']:.4f}"
    if detail["unpriced_runs"]:
        cost += f" + {detail['unpriced_runs']} unpriced runs"
    lines = [
        f"replay {detail['id']}  {detail['status']}  {detail['runs_uploaded']} runs  cost {cost}"
    ]
    if detail["item_id"]:
        lines.append(f"scored against item {detail['item_id'][:8]} rev {detail['item_rev']}:")
        width = max((len(c["scorer"]) for c in detail["pass_counts"]), default=0)
        for c in detail["pass_counts"]:
            mark = "pass" if c["passed"] == c["scored"] else "FAIL"
            lines.append(f"  {c['scorer']:<{width}}  {c['passed']}/{c['scored']}  {mark}")
        if not detail["pass_counts"]:
            lines.append("  no run was graded: every scorer skipped every run")
    if detail["errored"]:
        lines.append(f"{detail['errored']} runs errored (the agent raised or timed out)")
    where = f"{web_url.rstrip('/')}/replays/{detail['id']}" if web_url else None
    lines.append(f"side by side with the original: {where or 'the replay page in the web UI'}")
    return "\n".join(lines)
