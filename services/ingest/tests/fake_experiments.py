"""In-memory experiment storage with the same contract as `experiment_store.PostgresExperiments`.
Dataset versions and items come from a `FakeDatasets`, as the real store joins Postgres tables."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from uuid import UUID

from fake_datasets import FakeDatasets

from agenttrace.eval.results import NewExperiment
from agenttrace.pricing import PriceTable
from agenttrace_ingest.datasets import Conflict, Item, NotFound
from agenttrace_ingest.experiments import (
    Experiment,
    ExperimentInfo,
    SampleRow,
    ScoreRow,
    Status,
    StoredSample,
)


@dataclass
class _Experiment:
    project: UUID
    row: Experiment
    samples: dict[tuple[UUID, int], StoredSample] = field(default_factory=dict)


class FakeTx:
    def __init__(self, store: "FakeExperiments", e: _Experiment):
        self.store, self.e, self.experiment = store, e, e.row

    def version_items(self) -> dict[UUID, int]:
        r = self.e.row
        return self.store.version_items(self.e.project, r.dataset_id, r.dataset_version) or {}

    def count_samples(self) -> int:
        return len(self.e.samples)

    def upsert_samples(self, samples: list[StoredSample]) -> None:
        for s in samples:
            self.e.samples[(s.item_id, s.sample_index)] = s

    def finish(self, status: Status, error: str | None) -> None:
        self.e.row = self.e.row.model_copy(
            update={"status": status, "error": error, "finished_at": self.store.datasets.tick()}
        )


class FakeExperiments:
    def __init__(self, datasets: FakeDatasets):
        self.datasets = datasets
        self.experiments: dict[UUID, _Experiment] = {}

    def version_items(
        self, project: UUID, dataset_id: UUID, version: int
    ) -> dict[UUID, int] | None:
        d = self.datasets.datasets.get(dataset_id)
        if d is None or d.project != project or version not in d.versions:
            return None
        return dict(d.versions[version][1])

    def create(self, project: UUID, e: NewExperiment) -> None:
        if e.id in self.experiments:
            raise Conflict("an experiment with this id already exists")
        row = Experiment(
            **e.model_dump(exclude={"samples"}),
            samples=e.samples,
            status="running",
            error=None,
            started_at=self.datasets.tick(),
            finished_at=None,
        )
        self.experiments[e.id] = _Experiment(project, row)

    def _info(self, e: _Experiment) -> ExperimentInfo:
        r = e.row
        costs = [s.cost_usd for s in e.samples.values()]
        return ExperimentInfo(
            **dict(r),
            item_count=len(self.version_items(e.project, r.dataset_id, r.dataset_version) or {}),
            samples_uploaded=len(e.samples),
            cost_usd=sum(c for c in costs if c is not None),
            unpriced_samples=sum(c is None for c in costs),
            judge_cost_usd=sum(s.judge_cost_usd or 0 for s in e.samples.values()),
        )

    def get(self, project: UUID, experiment_id: UUID) -> ExperimentInfo | None:
        e = self.experiments.get(experiment_id)
        return self._info(e) if e and e.project == project else None

    def list_experiments(self, project: UUID, dataset_id: UUID | None) -> list[ExperimentInfo]:
        found = [
            self._info(e)
            for e in self.experiments.values()
            if e.project == project and dataset_id in (None, e.row.dataset_id)
        ]
        return sorted(found, key=lambda e: e.started_at, reverse=True)

    def latest_completed(
        self,
        project: UUID,
        config_hash: str,
        exclude: UUID,
        commit: str | None = None,
        branch: str | None = None,
    ) -> ExperimentInfo | None:
        for e in self.list_experiments(project, None):
            if (
                e.config_hash == config_hash
                and e.status == "completed"
                and e.id != exclude
                and commit in (None, e.git_commit)
                and branch in (None, e.git_branch)
            ):
                return e
        return None

    @contextmanager
    def open(self, project: UUID, experiment_id: UUID, exclusive: bool):
        e = self.experiments.get(experiment_id)
        if e is None or e.project != project:
            raise NotFound("no such experiment")
        yield FakeTx(self, e)

    def sample_rows(self, experiment_id: UUID) -> list[SampleRow]:
        return [
            SampleRow(
                item_id=s.item_id,
                sample_index=s.sample_index,
                errored=s.error is not None,
                latency_ms=s.latency_ms,
                cost_usd=s.cost_usd,
            )
            for s in self.experiments[experiment_id].samples.values()
        ]

    def score_rows(self, experiment_id: UUID) -> list[ScoreRow]:
        return [
            ScoreRow(
                item_id=s.item_id,
                sample_index=s.sample_index,
                scorer=sc.scorer,
                score=sc.score,
                passed=sc.passed,
            )
            for s in self.experiments[experiment_id].samples.values()
            for sc in s.scores
        ]

    def samples(self, experiment_id: UUID, item_id: UUID) -> list[StoredSample]:
        found = self.experiments[experiment_id].samples.values()
        return sorted((s for s in found if s.item_id == item_id), key=lambda s: s.sample_index)

    def item(self, item_id: UUID, rev: int) -> Item:
        for d in self.datasets.datasets.values():
            if item_id in d.items:
                return d.item(item_id, rev)
        raise AssertionError("a version's revision is never deleted")


class FakePrices:
    def __init__(self, table: PriceTable):
        self.table = table

    def get(self) -> PriceTable:
        return self.table
