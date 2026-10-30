"""In-memory replay storage with the same contract as `replay_store.PostgresReplays`. Items come
from a `FakeDatasets`, as the real store joins Postgres tables."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from uuid import UUID

from fake_datasets import FakeDatasets

from trayce.eval.results import NewReplay
from trayce_ingest.datasets import Conflict, Item, NotFound
from trayce_ingest.experiments import Status
from trayce_ingest.replays import PassCount, Replay, ReplayInfo, StoredReplayRun


@dataclass
class _Replay:
    project: UUID
    row: Replay
    runs: dict[int, StoredReplayRun] = field(default_factory=dict)


class FakeTx:
    def __init__(self, store: "FakeReplays", r: _Replay):
        self.store, self.r, self.replay = store, r, r.row

    def count_runs(self) -> int:
        return len(self.r.runs)

    def upsert_runs(self, runs: list[StoredReplayRun]) -> None:
        for run in runs:
            self.r.runs[run.run_index] = run

    def finish(self, status: Status, error: str | None) -> None:
        self.r.row = self.r.row.model_copy(
            update={"status": status, "error": error, "finished_at": self.store.datasets.tick()}
        )


class FakeReplays:
    def __init__(self, datasets: FakeDatasets):
        self.datasets = datasets
        self.replays: dict[UUID, _Replay] = {}

    def item(self, project: UUID, item_id: UUID, rev: int) -> Item | None:
        for d in self.datasets.datasets.values():
            if d.project == project and item_id in d.items and rev in d.items[item_id].revs:
                return d.item(item_id, rev)
        return None

    def create(self, project: UUID, r: NewReplay) -> None:
        if r.id in self.replays:
            raise Conflict("a replay with this id already exists")
        row = Replay(
            **r.model_dump(),
            status="running",
            error=None,
            started_at=self.datasets.tick(),
            finished_at=None,
        )
        self.replays[r.id] = _Replay(project, row)

    def _info(self, r: _Replay) -> ReplayInfo:
        runs = r.runs.values()
        costs = [run.cost_usd for run in runs]
        counts = []
        for spec in r.row.scorers:
            scores = [sc for run in runs for sc in run.scores if sc.scorer == spec.name]
            if scores:
                passed = sum(sc.passed for sc in scores)
                counts.append(PassCount(scorer=spec.name, passed=passed, scored=len(scores)))
        return ReplayInfo(
            **dict(r.row),
            runs_uploaded=len(r.runs),
            errored=sum(run.error is not None for run in runs),
            cost_usd=sum(c for c in costs if c is not None),
            unpriced_runs=sum(c is None for c in costs),
            judge_cost_usd=sum(run.judge_cost_usd or 0 for run in runs),
            pass_counts=counts,
        )

    def get(self, project: UUID, replay_id: UUID) -> ReplayInfo | None:
        r = self.replays.get(replay_id)
        return self._info(r) if r and r.project == project else None

    def list_for_trace(self, project: UUID, trace_id: str) -> list[ReplayInfo]:
        found = [
            self._info(r)
            for r in self.replays.values()
            if r.project == project
            and trace_id in (r.row.replay_of.trace_id, r.row.fork_of and r.row.fork_of.trace_id)
        ]
        return sorted(found, key=lambda r: r.started_at, reverse=True)

    @contextmanager
    def open(self, project: UUID, replay_id: UUID, exclusive: bool):
        r = self.replays.get(replay_id)
        if r is None or r.project != project:
            raise NotFound("no such replay")
        yield FakeTx(self, r)

    def runs(self, replay_id: UUID) -> list[StoredReplayRun]:
        return sorted(self.replays[replay_id].runs.values(), key=lambda r: r.run_index)
