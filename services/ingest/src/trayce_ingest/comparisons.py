"""Comparing an experiment with its baseline (ADR-0024): computed here so the PR comment and the
UI always agree.

The baseline is chosen per ADR-0007 and ADR-0026. Each scorer is one metric, its pass rate over
the items scored in both runs, and regresses when a paired item-level bootstrap puts the whole
95% interval of the change below zero and the drop exceeds the scorer's `max_drop` (ADR-0025).
"""

import random
import statistics
import threading
from collections import OrderedDict, defaultdict
from typing import Literal
from uuid import UUID

from trayce_ingest.errors import Conflict, NotFound
from trayce_ingest.experiments import ExperimentInfo, SampleRow, ScoreRow, Store, get
from trayce_ingest.records import Record

ROUNDS = 10_000
SEED = 20261025  # fixed, so a pair of experiments always compares the same way
BaselineReason = Literal["merge_base", "base_branch", "chosen"]
CACHED = 256  # comparisons kept in memory


class ScorerComparison(Record):
    scorer: str
    items: int  # scored in both runs: the denominator
    baseline_rate: float | None
    current_rate: float | None
    change: float | None  # current minus baseline
    ci_low: float | None
    ci_high: float | None
    max_drop: float
    regressed: bool
    underpowered: bool  # the interval is too wide to detect a drop of `max_drop`


class ItemRegression(Record):
    """Most samples passed this scorer in the baseline and most fail it now."""

    item_id: UUID
    scorer: str
    baseline_passed: int
    baseline_scored: int
    current_passed: int
    current_scored: int


class RunStats(Record):
    """Cost and latency, compared but never gating."""

    cost_per_sample: float | None  # mean over priced samples
    p50_latency_ms: float | None


class Comparison(Record):
    experiment: ExperimentInfo
    baseline: ExperimentInfo | None  # none found: report scores only, and pass
    baseline_reason: BaselineReason | None
    scorers: list[ScorerComparison]
    regressions: int
    item_regressions: list[ItemRegression]
    added_items: list[UUID]  # in this run's dataset version only; not compared
    removed_items: list[UUID]  # in the baseline's only
    current_stats: RunStats
    baseline_stats: RunStats | None


# --- The statistics -------------------------------------------------------------------------


Counts = tuple[int, int, int, int]  # baseline passed, scored; current passed, scored


def _change(rows: list[Counts]) -> float:
    bp = bn = cp = cn = 0
    for b_passed, b_scored, c_passed, c_scored in rows:
        bp, bn, cp, cn = bp + b_passed, bn + b_scored, cp + c_passed, cn + c_scored
    return cp / cn - bp / bn


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear interpolation between closest ranks, like numpy's default."""
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def bootstrap(rows: list[Counts], rounds: int = ROUNDS, seed: int = SEED) -> tuple[float, float]:
    """The 95% interval of the pass-rate change, resampling items (each with all its samples in
    both runs), since an item's samples are correlated (ADR-0025)."""
    rng = random.Random(seed)
    n = len(rows)
    changes = sorted(_change([rows[rng.randrange(n)] for _ in range(n)]) for _ in range(rounds))
    return _percentile(changes, 0.025), _percentile(changes, 0.975)


def _by_item(scores: list[ScoreRow]) -> dict[str, dict[UUID, tuple[int, int]]]:
    """`scorer -> item -> (passed, scored)`."""
    out: dict[str, dict[UUID, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for s in scores:
        c = out[s.scorer][s.item_id]
        c[0] += s.passed
        c[1] += 1
    return {k: {i: (p, n) for i, (p, n) in v.items()} for k, v in out.items()}


ItemCounts = dict[UUID, tuple[int, int]]  # item -> (passed, scored)


def compare_scorer(
    scorer: str, max_drop: float, base: ItemCounts, cur: ItemCounts
) -> tuple[ScorerComparison, list[ItemRegression]]:
    shared = [i for i in cur if i in base]
    rows = [(*base[i], *cur[i]) for i in shared]
    regressions = [
        ItemRegression(
            item_id=i,
            scorer=scorer,
            baseline_passed=r[0],
            baseline_scored=r[1],
            current_passed=r[2],
            current_scored=r[3],
        )
        for i, r in zip(shared, rows, strict=True)
        if 2 * r[0] > r[1] and 2 * (r[3] - r[2]) > r[3]
    ]
    if not rows:
        return ScorerComparison(
            scorer=scorer, items=0, baseline_rate=None, current_rate=None, change=None,
            ci_low=None, ci_high=None, max_drop=max_drop, regressed=False, underpowered=False,
        ), []  # fmt: skip
    low, high = bootstrap(rows)
    change = _change(rows)
    return ScorerComparison(
        scorer=scorer,
        items=len(rows),
        baseline_rate=sum(r[0] for r in rows) / sum(r[1] for r in rows),
        current_rate=sum(r[2] for r in rows) / sum(r[3] for r in rows),
        change=change,
        ci_low=low,
        ci_high=high,
        max_drop=max_drop,
        # A tiny tolerance, so a drop of exactly max_drop isn't a regression by rounding.
        regressed=high < 0 and -change > max_drop + 1e-9,
        underpowered=(high - low) / 2 > max_drop + 1e-9,
    ), regressions


def run_stats(samples: list[SampleRow]) -> RunStats:
    costs = [s.cost_usd for s in samples if s.cost_usd is not None]
    return RunStats(
        cost_per_sample=sum(costs) / len(costs) if costs else None,
        p50_latency_ms=statistics.median(s.latency_ms for s in samples) if samples else None,
    )


# --- Baselines and the comparison ----------------------------------------------------------


def find_baseline(
    store: Store, project: UUID, e: ExperimentInfo
) -> tuple[ExperimentInfo, BaselineReason] | None:
    """The newest completed experiment with the same hash at the merge-base commit, else on the
    base branch, else none (ADR-0007, ADR-0026)."""
    if e.git_merge_base:
        found = store.latest_completed(project, e.config_hash, e.id, commit=e.git_merge_base)
        if found:
            return found, "merge_base"
    if e.git_base_branch:
        found = store.latest_completed(project, e.config_hash, e.id, branch=e.git_base_branch)
        if found:
            return found, "base_branch"
    return None


class _Cache:
    """Computed comparisons by (experiment, baseline). Both are completed, so their samples can't
    change, and the bootstrap is the costly part (about 1.5 s per scorer at 1,000 items): the
    experiment page, the compare page, and the CLI all ask for the same pair."""

    def __init__(self, size: int = CACHED):
        self.size, self.lock = size, threading.Lock()
        self.items: OrderedDict[tuple[UUID, UUID], Comparison] = OrderedDict()

    def get(self, key: tuple[UUID, UUID]) -> Comparison | None:
        with self.lock:
            found = self.items.get(key)
            if found is not None:
                self.items.move_to_end(key)
            return found

    def put(self, key: tuple[UUID, UUID], c: Comparison) -> None:
        with self.lock:
            self.items[key] = c
            self.items.move_to_end(key)
            while len(self.items) > self.size:
                self.items.popitem(last=False)


_cache = _Cache()


def compare(
    store: Store, project: UUID, experiment_id: UUID, baseline_id: UUID | None = None
) -> Comparison:
    e = get(store, project, experiment_id)
    if e.status != "completed":
        raise Conflict(f"the experiment is {e.status}; only completed experiments are compared")
    found: tuple[ExperimentInfo, BaselineReason] | None
    if baseline_id is not None:
        b = store.get(project, baseline_id)
        if b is None:
            raise NotFound("no such baseline experiment")
        if b.status != "completed":
            raise Conflict(f"the baseline is {b.status}; only completed experiments are compared")
        if b.config_hash != e.config_hash:
            raise Conflict("the baseline ran a different eval.yaml (its hash differs)")
        found = b, "chosen"
    else:
        found = find_baseline(store, project, e)

    samples = store.sample_rows(e.id)
    if found is None:
        return Comparison(
            experiment=e, baseline=None, baseline_reason=None, scorers=[], regressions=0,
            item_regressions=[], added_items=[], removed_items=[],
            current_stats=run_stats(samples), baseline_stats=None,
        )  # fmt: skip
    b, reason = found
    cached = _cache.get((e.id, b.id))
    if cached is not None:
        return cached.model_copy(update={"baseline_reason": reason})
    base_scores, cur_scores = _by_item(store.score_rows(b.id)), _by_item(store.score_rows(e.id))
    cur_items = store.version_items(project, e.dataset_id, e.dataset_version) or {}
    base_items = store.version_items(project, b.dataset_id, b.dataset_version) or {}
    scorers, item_regressions = [], []
    for spec in e.scorers:
        cmp, regressed = compare_scorer(
            spec.name, spec.max_drop, base_scores.get(spec.name, {}), cur_scores.get(spec.name, {})
        )
        scorers.append(cmp)
        item_regressions += regressed
    order = {item: n for n, item in enumerate(cur_items)}
    item_regressions.sort(key=lambda r: order.get(r.item_id, len(order)))
    c = Comparison(
        experiment=e,
        baseline=b,
        baseline_reason=reason,
        scorers=scorers,
        regressions=sum(s.regressed for s in scorers),
        item_regressions=item_regressions,
        added_items=[i for i in cur_items if i not in base_items],
        removed_items=[i for i in base_items if i not in cur_items],
        current_stats=run_stats(samples),
        baseline_stats=run_stats(store.sample_rows(b.id)),
    )
    _cache.put((e.id, b.id), c)
    return c
