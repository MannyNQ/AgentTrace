"""Experiment rules (ADR-0023): one function per operation, as in `datasets`.

The eval runner creates an experiment as `running`, uploads samples as they're scored, and
finishes it. Storage sits behind `Store`/`Tx`, which only read and write rows; checks, pricing,
pass/fail, and summaries are decided here. The HTTP layer is in `experiment_api`, the Postgres
store in `experiment_store`. Refusals reuse the dataset errors, so both APIs answer alike."""

from collections import defaultdict
from collections.abc import Iterable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from agenttrace.eval.results import (
    AgentRun,
    Finish,
    ModelUsage,
    NewExperiment,
    SampleResult,
    ScorerSpec,
    ToolCall,
)
from agenttrace.pricing import PriceTable
from agenttrace_ingest.datasets import (
    MAX_JSON_BYTES,
    Conflict,
    Item,
    NotFound,
    Record,
    Refused,
    storable,
)

Status = Literal["running", "completed", "failed"]


# --- Records: what the store returns and the API serves ---------------------------------------


class Experiment(Record):
    id: UUID
    dataset_id: UUID
    dataset_version: int
    name: str | None
    status: Status
    error: str | None
    samples: int  # per item
    scorers: list[ScorerSpec]
    config: dict[str, Any]
    config_hash: str
    git_commit: str | None
    git_branch: str | None
    git_merge_base: str | None
    git_base_branch: str | None
    started_at: datetime
    finished_at: datetime | None


class ExperimentInfo(Experiment):
    """An experiment with its progress and cost, for listings."""

    item_count: int
    samples_uploaded: int
    cost_usd: float  # over priced samples
    unpriced_samples: int  # samples whose cost is unknown (ADR-0010)
    judge_cost_usd: float  # LLM judge calls, apart from the agent's cost (ADR-0027)


class StoredScore(Record):
    scorer: str
    score: float
    passed: bool
    reason: str | None


class StoredRun(Record):
    """An agent run as stored, priced, and graded: an experiment's sample or a replay run."""

    trace_id: str | None
    started_at: datetime
    latency_ms: float
    output: Any
    output_truncated: bool
    trajectory: list[ToolCall]
    error: str | None
    usage: list[ModelUsage]
    cost_usd: float | None
    judge_usage: list[ModelUsage]
    judge_cost_usd: float | None
    scores: list[StoredScore]


class StoredSample(StoredRun):
    item_id: UUID
    sample_index: int


class ScoreRow(Record):
    """One score without the sample's payloads, for summaries."""

    item_id: UUID
    sample_index: int
    scorer: str
    score: float
    passed: bool


class SampleRow(Record):
    item_id: UUID
    sample_index: int
    errored: bool
    latency_ms: float
    cost_usd: float | None


class ScorerStats(Record):
    scorer: str
    scored: int  # samples graded; items lacking the scorer's expectation are skipped
    passed: int
    pass_rate: float | None
    mean_score: float | None
    pass_rate_by_sample: list[float | None]  # by sample_index: the spread across repeats


class ItemScore(Record):
    passed: int
    scored: int


class ItemSummary(Record):
    item_id: UUID
    rev: int
    samples_uploaded: int
    errored: int
    mean_latency_ms: float | None
    scores: dict[str, ItemScore]


class ExperimentDetail(ExperimentInfo):
    scorer_stats: list[ScorerStats]
    items: list[ItemSummary]


class ItemDetail(Record):
    item: Item  # at the revision the dataset version holds
    samples: list[StoredSample]


# --- Storage interface ----------------------------------------------------------------------


class Tx(Protocol):
    """Reads and writes one experiment inside a transaction; makes no decisions."""

    experiment: Experiment

    def version_items(self) -> dict[UUID, int]:
        """The `item_id -> rev` pointers of the dataset version the experiment ran on."""
        ...

    def count_samples(self) -> int: ...

    def upsert_samples(self, samples: list[StoredSample]) -> None:
        """Replaces each sample, and its scores, keyed by `(item_id, sample_index)`."""
        ...

    def finish(self, status: Status, error: str | None) -> None: ...


class Store(Protocol):
    def version_items(
        self, project: UUID, dataset_id: UUID, version: int
    ) -> dict[UUID, int] | None:
        """A dataset version's `item_id -> rev` pointers, or None when the project has no such
        dataset version."""
        ...

    def create(self, project: UUID, e: NewExperiment) -> None:
        """Raises Conflict when the ID is taken, in any project."""
        ...

    def get(self, project: UUID, experiment_id: UUID) -> ExperimentInfo | None: ...

    def list_experiments(self, project: UUID, dataset_id: UUID | None) -> list[ExperimentInfo]:
        """Newest first."""
        ...

    def latest_completed(
        self,
        project: UUID,
        config_hash: str,
        exclude: UUID,
        commit: str | None = None,
        branch: str | None = None,
    ) -> ExperimentInfo | None:
        """The newest completed experiment with this hash, other than `exclude`, at `commit`
        and on `branch` when they're given."""
        ...

    def open(
        self, project: UUID, experiment_id: UUID, exclusive: bool
    ) -> AbstractContextManager[Tx]:
        """A transaction on one experiment. Uploads share its row lock and a finish holds it
        exclusively, so a finish counts samples nobody is adding. Raises NotFound."""
        ...

    def sample_rows(self, experiment_id: UUID) -> list[SampleRow]: ...

    def score_rows(self, experiment_id: UUID) -> list[ScoreRow]: ...

    def samples(self, experiment_id: UUID, item_id: UUID) -> list[StoredSample]:
        """In `sample_index` order."""
        ...

    def item(self, item_id: UUID, rev: int) -> Item: ...


class Prices(Protocol):
    def get(self) -> PriceTable: ...


# --- Rules ----------------------------------------------------------------------------------


def _same_run(e: Experiment, new: NewExperiment) -> bool:
    """A retried create matches what's stored; anything else reusing the ID is a conflict."""
    return (
        e.dataset_id == new.dataset_id
        and e.dataset_version == new.dataset_version
        and e.samples == new.samples
        and e.scorers == new.scorers
        and e.config_hash == new.config_hash
    )


def get(store: Store, project: UUID, experiment_id: UUID) -> ExperimentInfo:
    e = store.get(project, experiment_id)
    if e is None:
        raise NotFound("no such experiment")
    return e


def create(store: Store, project: UUID, new: NewExperiment) -> tuple[ExperimentInfo, bool]:
    """The experiment, and whether this call created it. Retrying a create returns the original."""
    existing = store.get(project, new.id)
    if existing is not None:
        if not _same_run(existing, new):
            raise Conflict("an experiment with this id already exists with different settings")
        return existing, False
    if store.version_items(project, new.dataset_id, new.dataset_version) is None:
        raise NotFound(f"no such dataset version: v{new.dataset_version}")
    store.create(project, new)
    return get(store, project, new.id), True


def list_experiments(store: Store, project: UUID, dataset_id: UUID | None) -> list[ExperimentInfo]:
    return store.list_experiments(project, dataset_id)


def sample_cost(prices: PriceTable, usage: Iterable[ModelUsage], at: datetime) -> float | None:
    """The sum over models, priced at the sample's start. Unknown if any model is (ADR-0010)."""
    total = 0.0
    for u in usage:
        cost = prices.cost(u, at)
        if cost is None:
            return None
        total += cost
    return total


def check_text(value: str | None, field: str) -> None:
    if value is not None and "\x00" in value:
        raise Refused(f"{field} has a NUL character (\\u0000), which Postgres can't store")


def stored[T: StoredRun](
    cls: type[T], r: AgentRun, thresholds: dict[str, float], prices: PriceTable, **refused: Any
) -> T:
    """`r` as `cls` stores it: checked for what Postgres can hold, priced at its start
    (ADR-0010), and each score marked passed or not. `refused` goes on a refusal's detail."""
    try:
        storable(r.output, "output", MAX_JSON_BYTES)
        storable([t.model_dump() for t in r.trajectory], "trajectory")
    except ValueError as e:
        raise Refused(str(e), **refused) from e
    check_text(r.error, "error")
    for sc in r.scores:
        check_text(sc.reason, "reason")
    return cls(
        **r.model_dump(exclude={"scores"}),
        cost_usd=sample_cost(prices, r.usage, r.started_at),
        judge_cost_usd=sample_cost(prices, r.judge_usage, r.started_at),
        scores=[
            StoredScore(
                scorer=sc.scorer,
                score=sc.score,
                passed=sc.score >= thresholds[sc.scorer],
                reason=sc.reason,
            )
            for sc in r.scores
        ],
    )


def upload_samples(
    store: Store, prices: Prices, project: UUID, experiment_id: UUID, samples: list[SampleResult]
) -> int:
    """Upserts samples into a running experiment; a retried upload changes nothing."""
    with store.open(project, experiment_id, exclusive=False) as tx:
        e = tx.experiment
        if e.status != "running":
            raise Conflict(f"the experiment is {e.status}; its results can't change")
        items = tx.version_items()
        unknown = [s.item_id for s in samples if s.item_id not in items]
        if unknown:
            raise Refused(
                f"{len(unknown)} samples are for items not in v{e.dataset_version}", items=unknown
            )
        keys = [(s.item_id, s.sample_index) for s in samples]
        if len(keys) != len(set(keys)):
            raise Refused("a sample appears twice in one upload")
        if any(s.sample_index >= e.samples for s in samples):
            raise Refused(f"sample_index must be below the experiment's samples ({e.samples})")
        thresholds = {sc.name: sc.threshold for sc in e.scorers}
        scorers = {sc.scorer for s in samples for sc in s.scores}
        if scorers - thresholds.keys():
            raise Refused(f"unknown scorers: {', '.join(sorted(scorers - thresholds.keys()))}")
        table = prices.get()
        tx.upsert_samples(
            [stored(StoredSample, s, thresholds, table, item_id=s.item_id) for s in samples]
        )
    return len(samples)


def finish(store: Store, project: UUID, experiment_id: UUID, body: Finish) -> ExperimentInfo:
    """`completed` needs every sample of every item; `failed` is always allowed while running.
    Finishing again with the same status is a no-op, so a retried finish succeeds."""
    with store.open(project, experiment_id, exclusive=True) as tx:
        e = tx.experiment
        if e.status != body.status:
            if e.status != "running":
                raise Conflict(f"the experiment is already {e.status}")
            if body.status == "completed":
                expected = len(tx.version_items()) * e.samples
                got = tx.count_samples()
                if got < expected:
                    raise Refused(
                        f"{got} of {expected} samples uploaded; upload the rest or finish as failed"
                    )
            check_text(body.error, "error")
            tx.finish(body.status, body.error)
    return get(store, project, experiment_id)


def _rate(passed: int, scored: int) -> float | None:
    return passed / scored if scored else None


def summarize(
    e: Experiment,
    items: dict[UUID, int],
    samples: list[SampleRow],
    scores: list[ScoreRow],
) -> tuple[list[ScorerStats], list[ItemSummary]]:
    """Per-scorer pass rates (with each repeat's rate, for spread) and per-item pass counts."""
    stats = []
    for spec in e.scorers:
        mine = [s for s in scores if s.scorer == spec.name]
        by_index = [[s for s in mine if s.sample_index == i] for i in range(e.samples)]
        passed = sum(s.passed for s in mine)
        stats.append(
            ScorerStats(
                scorer=spec.name,
                scored=len(mine),
                passed=passed,
                pass_rate=_rate(passed, len(mine)),
                mean_score=sum(s.score for s in mine) / len(mine) if mine else None,
                pass_rate_by_sample=[_rate(sum(s.passed for s in b), len(b)) for b in by_index],
            )
        )

    samples_by_item: dict[UUID, list[SampleRow]] = defaultdict(list)
    for s in samples:
        samples_by_item[s.item_id].append(s)
    counts: dict[UUID, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for s in scores:
        c = counts[s.item_id][s.scorer]
        c[0] += s.passed
        c[1] += 1
    summaries = []
    for item_id, rev in items.items():
        mine = samples_by_item[item_id]
        summaries.append(
            ItemSummary(
                item_id=item_id,
                rev=rev,
                samples_uploaded=len(mine),
                errored=sum(s.errored for s in mine),
                mean_latency_ms=sum(s.latency_ms for s in mine) / len(mine) if mine else None,
                scores={
                    name: ItemScore(passed=p, scored=n) for name, (p, n) in counts[item_id].items()
                },
            )
        )
    return stats, summaries


def detail(store: Store, project: UUID, experiment_id: UUID) -> ExperimentDetail:
    e = get(store, project, experiment_id)
    items = store.version_items(project, e.dataset_id, e.dataset_version) or {}
    stats, summaries = summarize(
        e, items, store.sample_rows(experiment_id), store.score_rows(experiment_id)
    )
    return ExperimentDetail(**dict(e), scorer_stats=stats, items=summaries)


def item_detail(store: Store, project: UUID, experiment_id: UUID, item_id: UUID) -> ItemDetail:
    """One item as the dataset version holds it, with every sample's output and scores."""
    e = get(store, project, experiment_id)
    items = store.version_items(project, e.dataset_id, e.dataset_version) or {}
    if item_id not in items:
        raise NotFound("no such item in the experiment's dataset version")
    return ItemDetail(
        item=store.item(item_id, items[item_id]), samples=store.samples(experiment_id, item_id)
    )
