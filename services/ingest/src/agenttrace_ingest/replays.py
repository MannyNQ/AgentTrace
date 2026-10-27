"""Replay rules (ADR-0029): one function per operation, as in `experiments`.

`agenttrace replay` creates a replay as `running`, uploads replay runs as they're scored, and
finishes it. A scored replay names the draft revision of the item it's scored against (ADR-0030).
Storage sits behind `Store`/`Tx`; the HTTP layer is in `replay_api`, the Postgres store in
`replay_store`. Refusals reuse the dataset errors, so every API answers alike."""

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from agenttrace.eval.results import (
    Finish,
    NewReplay,
    ReplayRunResult,
    ScorerSpec,
    SpanRef,
    StepWarning,
    ToolMode,
)
from agenttrace_ingest.datasets import Conflict, Item, NotFound, Record, Refused
from agenttrace_ingest.experiments import Prices, Status, StoredRun, check_text, stored
from agenttrace_ingest.trace_inputs import TraceRoots

# --- Records: what the store returns and the API serves ---------------------------------------


class Replay(Record):
    id: UUID
    replay_of: SpanRef  # the original trace's root span, for a fork as for a whole replay
    fork_of: SpanRef | None  # a fork's fork point, in the trace it forked (ADR-0040)
    tool_mode: ToolMode
    status: Status
    error: str | None
    runs: int
    item_id: UUID | None
    item_rev: int | None
    scorers: list[ScorerSpec]
    config: dict[str, Any] | None
    config_hash: str | None
    git_commit: str | None
    git_branch: str | None
    git_dirty: bool | None
    started_at: datetime
    finished_at: datetime | None


class PassCount(Record):
    scorer: str
    passed: int
    scored: int  # runs graded; a scorer lacking its expectation is skipped


class ReplayInfo(Replay):
    """A replay with its progress, cost, and pass counts, for the trace page's listing."""

    runs_uploaded: int
    errored: int
    cost_usd: float  # over priced runs
    unpriced_runs: int  # runs whose cost is unknown (ADR-0010)
    judge_cost_usd: float
    pass_counts: list[PassCount]  # in scorer order; empty when unscored


class StoredReplayRun(StoredRun):
    run_index: int
    diverged_at: int | None
    warnings: list[StepWarning]


class ReplayDetail(ReplayInfo):
    item: Item | None  # the revision it was scored against; None when unscored or deleted since
    replay_runs: list[StoredReplayRun]  # in run_index order


# --- Storage interface ----------------------------------------------------------------------


class Tx(Protocol):
    """Reads and writes one replay inside a transaction; makes no decisions."""

    replay: Replay

    def count_runs(self) -> int: ...

    def upsert_runs(self, runs: list[StoredReplayRun]) -> None:
        """Replaces each run, and its scores, keyed by `run_index`."""
        ...

    def finish(self, status: Status, error: str | None) -> None: ...


class Store(Protocol):
    def item(self, project: UUID, item_id: UUID, rev: int) -> Item | None:
        """An item revision in one of the project's datasets, or None."""
        ...

    def create(self, project: UUID, r: NewReplay) -> None:
        """Raises Conflict when the ID is taken, in any project."""
        ...

    def get(self, project: UUID, replay_id: UUID) -> ReplayInfo | None: ...

    def list_for_trace(self, project: UUID, trace_id: str) -> list[ReplayInfo]:
        """Replays of one original trace, and forks of it whatever trace they forked, plus forks
        of it when it's a replay run; newest first."""
        ...

    def open(self, project: UUID, replay_id: UUID, exclusive: bool) -> AbstractContextManager[Tx]:
        """A transaction on one replay. Uploads share its row lock and a finish holds it
        exclusively, as for experiments. Raises NotFound."""
        ...

    def runs(self, replay_id: UUID) -> list[StoredReplayRun]:
        """In `run_index` order."""
        ...


# --- Rules ----------------------------------------------------------------------------------


def _same_replay(r: Replay, new: NewReplay) -> bool:
    """A retried create matches what's stored; anything else reusing the ID is a conflict."""
    return (
        r.replay_of == new.replay_of
        and r.fork_of == new.fork_of
        and r.tool_mode == new.tool_mode
        and r.runs == new.runs
        and r.item_id == new.item_id
        and r.item_rev == new.item_rev
        and r.scorers == new.scorers
        and r.config_hash == new.config_hash
    )


def get(store: Store, project: UUID, replay_id: UUID) -> ReplayInfo:
    r = store.get(project, replay_id)
    if r is None:
        raise NotFound("no such replay")
    return r


def create(
    store: Store, roots: TraceRoots, project: UUID, new: NewReplay
) -> tuple[ReplayInfo, bool]:
    """The replay, and whether this call created it. Retrying a create returns the original.
    The trace it re-runs must be in the project, so one project can't file replays under
    another's traces. Once that original expires, a replay of one of its replay runs still names
    it; the project's earlier replay of it vouches for it then. A fork's trace must still exist,
    since its steps are what it plays back, and must lead back to the original the fork names,
    so a fork is listed under the trace it really re-ran."""
    existing = store.get(project, new.id)
    if existing is not None:
        if not _same_replay(existing, new):
            raise Conflict("a replay with this id already exists with different settings")
        return existing, False
    original = new.replay_of.trace_id
    if not roots.exists(project, original) and not store.list_for_trace(project, original):
        raise NotFound("trace not found; it may have expired")
    if new.fork_of is not None:
        origin = roots.origin(project, new.fork_of.trace_id)
        if origin.trace_id != original:
            raise Refused(
                f"the forked trace re-ran {origin.trace_id}, so that's the original to name"
            )
    if new.item_id is not None and new.item_rev is not None:
        item = store.item(project, new.item_id, new.item_rev)
        if item is None:
            raise NotFound(f"no such item revision: rev {new.item_rev}")
        if item.source_trace_id != new.replay_of.trace_id:
            raise Refused("a replay is scored only against the item made from the trace it re-ran")
    store.create(project, new)
    return get(store, project, new.id), True


def list_for_trace(store: Store, project: UUID, trace_id: str) -> list[ReplayInfo]:
    return store.list_for_trace(project, trace_id)


def upload_runs(
    store: Store, prices: Prices, project: UUID, replay_id: UUID, runs: list[ReplayRunResult]
) -> int:
    """Upserts runs into a running replay; a retried upload changes nothing."""
    with store.open(project, replay_id, exclusive=False) as tx:
        r = tx.replay
        if r.status != "running":
            raise Conflict(f"the replay is {r.status}; its results can't change")
        indexes = [run.run_index for run in runs]
        if len(indexes) != len(set(indexes)):
            raise Refused("a run appears twice in one upload")
        if any(i >= r.runs for i in indexes):
            raise Refused(f"run_index must be below the replay's runs ({r.runs})")
        thresholds = {sc.name: sc.threshold for sc in r.scorers}
        scorers = {sc.scorer for run in runs for sc in run.scores}
        if scorers - thresholds.keys():
            raise Refused(f"unknown scorers: {', '.join(sorted(scorers - thresholds.keys()))}")
        table = prices.get()
        tx.upsert_runs([stored(StoredReplayRun, run, thresholds, table) for run in runs])
    return len(runs)


def finish(store: Store, project: UUID, replay_id: UUID, body: Finish) -> ReplayInfo:
    """`completed` needs every run; `failed` is always allowed while running. Finishing again
    with the same status is a no-op, so a retried finish succeeds."""
    with store.open(project, replay_id, exclusive=True) as tx:
        r = tx.replay
        if r.status != body.status:
            if r.status != "running":
                raise Conflict(f"the replay is already {r.status}")
            if body.status == "completed":
                got = tx.count_runs()
                if got < r.runs:
                    raise Refused(
                        f"{got} of {r.runs} runs uploaded; upload the rest or finish as failed"
                    )
            check_text(body.error, "error")
            tx.finish(body.status, body.error)
    return get(store, project, replay_id)


def detail(store: Store, project: UUID, replay_id: UUID) -> ReplayDetail:
    r = get(store, project, replay_id)
    item = None
    if r.item_id is not None and r.item_rev is not None:
        item = store.item(project, r.item_id, r.item_rev)
    return ReplayDetail(**dict(r), item=item, replay_runs=store.runs(replay_id))
