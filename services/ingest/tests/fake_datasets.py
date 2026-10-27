"""In-memory dataset storage with the same contract as `dataset_store.PostgresDatasets`. Like
the real store, it only keeps rows; the rules under test are in `agenttrace_ingest.datasets`."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from agenttrace.eval.results import SpanRef
from agenttrace_ingest.datasets import (
    Conflict,
    Contents,
    Dataset,
    DatasetRow,
    Item,
    NotFound,
    Refused,
    VersionInfo,
)
from agenttrace_ingest.trace_inputs import RootInput, resolve_origin


@dataclass
class _Item:
    source_trace_id: str | None
    created_at: datetime
    revs: dict[int, Contents] = field(default_factory=dict)
    current_rev: int = 0
    deleted: bool = False
    pinned: bool = False


@dataclass
class _Dataset:
    project: UUID
    row: DatasetRow
    items: dict[UUID, _Item] = field(default_factory=dict)
    versions: dict[int, tuple[VersionInfo, dict[UUID, int]]] = field(default_factory=dict)

    def item(self, item_id: UUID, rev: int) -> Item:
        i = self.items[item_id]
        return Item(
            item_id=item_id,
            rev=rev,
            source_trace_id=i.source_trace_id,
            created_at=i.created_at,
            contents=i.revs[rev],
        )


class FakeTx:
    def __init__(self, store: "FakeDatasets", d: _Dataset):
        self.store, self.d, self.dataset = store, d, d.row

    def update(self, name, description, archived) -> None:
        holder = self.store.holder(self.d.project, name) if name else None
        if holder is not None and holder != self.d.row.id:
            raise Conflict(f'a dataset named "{name}" already exists', dataset_id=holder)
        changes = {"name": name, "description": description, "archived": archived}
        self.d.row = self.d.row.model_copy(
            update={k: v for k, v in changes.items() if v is not None}
        )

    def draft(self) -> list[Item]:
        return [
            self.d.item(item_id, i.current_rev)
            for item_id, i in self.d.items.items()
            if not i.deleted
        ]

    def draft_item(self, item_id: UUID) -> Item | None:
        i = self.d.items.get(item_id)
        return self.d.item(item_id, i.current_rev) if i and not i.deleted else None

    def item_for_trace(self, trace_id: str) -> UUID | None:
        return next(
            (k for k, i in self.d.items.items() if i.source_trace_id == trace_id and not i.deleted),
            None,
        )

    def latest(self):
        if not self.d.versions:
            return None
        n = max(self.d.versions)
        return n, dict(self.d.versions[n][1])

    def versions(self) -> list[VersionInfo]:
        return [self.d.versions[n][0] for n in sorted(self.d.versions, reverse=True)]

    def version(self, n: int):
        if n not in self.d.versions:
            return None
        info, entries = self.d.versions[n]
        items = [self.d.item(item_id, rev) for item_id, rev in entries.items()]
        return info, sorted(items, key=lambda i: i.created_at)

    def insert_item(self, source_trace_id: str | None) -> UUID:
        item_id = uuid4()
        self.d.items[item_id] = _Item(source_trace_id, self.store.tick())
        return item_id

    def insert_revision(self, item_id: UUID, rev: int, contents: Contents) -> None:
        i = self.d.items[item_id]
        assert rev not in i.revs, "primary key (item_id, rev)"
        i.revs[rev] = contents
        i.current_rev = rev

    def is_published(self, item_id: UUID) -> bool:
        return any(item_id in entries for _, entries in self.d.versions.values())

    def remove_item(self, item_id: UUID, hard: bool) -> None:
        if hard:
            del self.d.items[item_id]
        else:
            self.d.items[item_id].deleted = True

    def insert_version(self, n: int, entries: dict[UUID, int]) -> None:
        assert n not in self.d.versions, "primary key (dataset_id, version)"
        info = VersionInfo(version=n, item_count=len(entries), published_at=self.store.tick())
        self.d.versions[n] = (info, dict(entries))


class FakeDatasets:
    def __init__(self):
        self.datasets: dict[UUID, _Dataset] = {}
        self._clock = 0

    def tick(self) -> datetime:
        self._clock += 1
        return datetime.fromtimestamp(1_800_000_000 + self._clock, UTC)

    def holder(self, project: UUID, name: str) -> UUID | None:
        """The dataset with this name, as the unique (project_id, name) index finds it."""
        return next(
            (
                d.row.id
                for d in self.datasets.values()
                if d.project == project and d.row.name == name
            ),
            None,
        )

    def _summary(self, d: _Dataset) -> Dataset:
        return Dataset(
            **dict(d.row),
            latest_version=max(d.versions, default=None),
            draft_items=sum(not i.deleted for i in d.items.values()),
        )

    def list_datasets(self, project: UUID, include_archived: bool) -> list[Dataset]:
        found = [
            self._summary(d)
            for d in self.datasets.values()
            if d.project == project and (include_archived or not d.row.archived)
        ]
        return sorted(found, key=lambda d: d.name)

    def get_dataset(self, project: UUID, dataset_id: UUID) -> Dataset | None:
        d = self.datasets.get(dataset_id)
        return self._summary(d) if d and d.project == project else None

    def create_dataset(self, project: UUID, name: str, description: str) -> UUID:
        holder = self.holder(project, name)
        if holder is not None:
            raise Conflict(f'a dataset named "{name}" already exists', dataset_id=holder)
        row = DatasetRow(
            id=uuid4(), name=name, description=description, archived=False, created_at=self.tick()
        )
        self.datasets[row.id] = _Dataset(project, row)
        return row.id

    def unpinned(self, limit: int) -> list[tuple[UUID, str]]:
        found: list[tuple[UUID, str]] = []
        for d in self.datasets.values():
            for i in sorted(d.items.values(), key=lambda i: i.created_at):
                if i.source_trace_id is None or i.pinned:
                    continue
                if (d.project, i.source_trace_id) not in found:
                    found.append((d.project, i.source_trace_id))
        return found[:limit]

    def mark_pinned(self, project: UUID, trace_ids: list[str]) -> None:
        for d in self.datasets.values():
            for i in d.items.values():
                if d.project == project and i.source_trace_id in trace_ids:
                    i.pinned = True

    @contextmanager
    def open(self, project: UUID, dataset_id: UUID, lock: bool):
        d = self.datasets.get(dataset_id)
        if d is None or d.project != project:
            raise NotFound("no such dataset")
        yield FakeTx(self, d)


class FakeTraces:
    """Root inputs by trace ID; a NotFound or Refused value is raised instead. A trace's root span
    ID is the first half of its trace ID; `replay_of` marks traces that are replay runs. With
    `project`, every trace belongs to that project only; without it, to every project."""

    def __init__(
        self,
        inputs: dict[str, Any],
        replay_of: dict[str, SpanRef] | None = None,
        project: UUID | None = None,
    ):
        self.inputs, self.replay_of, self.project = inputs, replay_of or {}, project

    def exists(self, project: UUID, trace_id: str) -> bool:
        mine = self.project is None or project == self.project
        return mine and not isinstance(self.inputs.get(trace_id, NotFound("")), NotFound)

    def root_input(self, project: UUID, trace_id: str) -> Any:
        value = self.inputs.get(trace_id, NotFound("trace not found; it may have expired"))
        if isinstance(value, NotFound | Refused):
            raise value
        return value

    def root(self, project: UUID, trace_id: str) -> RootInput:
        value = self.root_input(project, trace_id)
        parent = self.replay_of.get(trace_id)
        return RootInput(
            trace_id=trace_id,
            span_id=trace_id[:16],
            input=value,
            replay_of=None if parent is None else resolve_origin(parent, self._lookup(project)),
        )

    def origin(self, project: UUID, trace_id: str) -> SpanRef:
        if not self.exists(project, trace_id):
            raise NotFound("trace not found; it may have expired")
        return resolve_origin(
            SpanRef(trace_id=trace_id, span_id=trace_id[:16]), self._lookup(project)
        )

    def _lookup(self, project: UUID):
        def lookup(trace_id: str) -> tuple[str, SpanRef | None] | None:
            if not self.exists(project, trace_id):
                return None
            return trace_id[:16], self.replay_of.get(trace_id)

        return lookup


class FakePins:
    """Records pinned traces per project; `down` makes every pin fail, like a ClickHouse outage."""

    def __init__(self):
        self.pinned: list[tuple[UUID, str]] = []
        self.down = False

    def pin(self, project: UUID, trace_ids: list[str]) -> None:
        if self.down:
            raise ConnectionError("clickhouse is down")
        self.pinned += [(project, t) for t in trace_ids]
