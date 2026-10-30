"""Dataset rules (ADR-0019, ADR-0020): one function per operation, each written once.

Storage sits behind `Store`/`Tx`, which only read and write rows; every decision (revision and
version numbers, the delete policy, the archived rule, publish checks) is made here. The HTTP
layer is in `dataset_api`, the Postgres store in `dataset_store`."""

import json
import logging
from collections import defaultdict
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal, Protocol
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from trayce.eval.results import ToolCall

log = logging.getLogger(__name__)

MAX_JSON_BYTES = 1024 * 1024  # input and expected_output, each (architecture.md, Phase 2)

Name = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
TraceId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Status = Literal["new", "changed", "unchanged"]
Change = Literal["added", "changed", "unchanged"]


# --- Contents -------------------------------------------------------------------------------


class Contents(BaseModel):
    """What an item revision holds."""

    model_config = ConfigDict(extra="forbid")
    input: Any
    expected_output: Any = None
    expected_trajectory: list[ToolCall] | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("input")
    @classmethod
    def _input(cls, v: Any) -> Any:
        if v is None:
            raise ValueError("input is required")
        return storable(v, "input", MAX_JSON_BYTES)

    @field_validator("expected_output")
    @classmethod
    def _expected_output(cls, v: Any) -> Any:
        return v if v is None else storable(v, "expected_output", MAX_JSON_BYTES)

    @field_validator("expected_trajectory")
    @classmethod
    def _trajectory(cls, v: list[ToolCall] | None) -> list[ToolCall] | None:
        if v is not None:
            storable([t.model_dump() for t in v], "expected_trajectory")
        return v

    @field_validator("metadata")
    @classmethod
    def _metadata(cls, v: dict[str, str]) -> dict[str, str]:
        return storable(v, "metadata")

    def has_expectation(self) -> bool:
        return self.expected_output is not None or self.expected_trajectory is not None


def _has_nul(v: Any) -> bool:
    if isinstance(v, str):
        return "\x00" in v
    if isinstance(v, dict):
        return any(_has_nul(k) or _has_nul(x) for k, x in v.items())
    if isinstance(v, list):
        return any(_has_nul(x) for x in v)
    return False


def storable(v: Any, field: str, max_bytes: int | None = None) -> Any:
    """Postgres `jsonb` holds standard JSON only: no NaN or Infinity, and no NUL characters."""
    try:
        text = json.dumps(v, ensure_ascii=False, allow_nan=False)
    except ValueError as e:
        raise ValueError(f"{field} has NaN or Infinity, which JSON can't hold") from e
    if _has_nul(v):
        raise ValueError(f"{field} has a NUL character (\\u0000), which Postgres can't store")
    if max_bytes is not None and len(text.encode()) > max_bytes:
        size = len(text.encode())
        raise ValueError(f"{field} is {size} bytes of JSON; the limit is {max_bytes}")
    return v


def _no_nul(v: str) -> str:
    if "\x00" in v:
        raise ValueError("has a NUL character (\\u0000), which Postgres can't store")
    return v


Text = Annotated[str, AfterValidator(_no_nul)]  # free text stored in a Postgres TEXT column


# --- Records: what the store returns and the API serves ---------------------------------------


class Record(BaseModel):
    model_config = ConfigDict(frozen=True)


class DatasetRow(Record):
    id: UUID
    name: str
    description: str
    archived: bool
    created_at: datetime


class Dataset(DatasetRow):
    """A dataset with its summary counts, for listings."""

    latest_version: int | None
    draft_items: int


class Item(Record):
    """An item at one revision: a draft item, or an entry of a version."""

    item_id: UUID
    rev: int
    source_trace_id: str | None
    created_at: datetime
    contents: Contents


class DraftEntry(Item):
    status: Status
    needs_expectation: bool


class Draft(Record):
    latest_version: int | None
    items: list[DraftEntry]


class VersionInfo(Record):
    version: int
    item_count: int
    published_at: datetime


class VersionEntry(Item):
    change: Change


class VersionDiff(VersionInfo):
    items: list[VersionEntry]
    removed: list[Item]  # as of the previous version


# --- Errors ---------------------------------------------------------------------------------


class DatasetError(Exception):
    """A refusal the API returns as `{"detail": {"error": ..., ...}}` with `status`."""

    status: ClassVar[int] = 422

    def __init__(
        self,
        error: str,
        *,
        items: list[UUID] | None = None,
        item_id: UUID | None = None,
        dataset_id: UUID | None = None,
        rev: int | None = None,
    ):
        super().__init__(error)
        self.error, self.items, self.rev = error, items or [], rev
        self.item_id, self.dataset_id = item_id, dataset_id

    def detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {"error": self.error}
        if self.items:
            detail["items"] = [str(i) for i in self.items]
        if self.item_id is not None:
            detail["item_id"] = str(self.item_id)
        if self.dataset_id is not None:
            detail["dataset_id"] = str(self.dataset_id)
        if self.rev is not None:
            detail["rev"] = self.rev
        return detail


class NotFound(DatasetError):
    status = 404


class Conflict(DatasetError):
    status = 409


class Refused(DatasetError):
    """Well-formed, but breaks a rule."""

    status = 422


# --- Storage interface ----------------------------------------------------------------------


class Tx(Protocol):
    """Reads and writes rows of one dataset inside a transaction; makes no decisions."""

    dataset: DatasetRow

    def update(self, name: str | None, description: str | None, archived: bool | None) -> None:
        """Changes the given fields. Raises Conflict, naming the dataset that holds the name,
        when it's taken."""
        ...

    def draft(self) -> list[Item]: ...

    def draft_item(self, item_id: UUID) -> Item | None: ...

    def item_for_trace(self, trace_id: str) -> UUID | None:
        """The live draft item copied from this trace, if any."""
        ...

    def latest(self) -> tuple[int, dict[UUID, int]] | None:
        """The latest version's number and its `item_id -> rev` pointers."""
        ...

    def versions(self) -> list[VersionInfo]: ...

    def version(self, n: int) -> tuple[VersionInfo, list[Item]] | None: ...

    def insert_item(self, source_trace_id: str | None) -> UUID:
        """A new item with no revision yet; `insert_revision` gives it one."""
        ...

    def insert_revision(self, item_id: UUID, rev: int, contents: Contents) -> None:
        """Stores the revision and makes it the item's draft revision."""
        ...

    def is_published(self, item_id: UUID) -> bool: ...

    def remove_item(self, item_id: UUID, hard: bool) -> None:
        """Drops the item from the draft; `hard` deletes it and its revisions."""
        ...

    def insert_version(self, n: int, entries: dict[UUID, int]) -> None: ...


class Store(Protocol):
    def list_datasets(self, project: UUID, include_archived: bool) -> list[Dataset]: ...

    def get_dataset(self, project: UUID, dataset_id: UUID) -> Dataset | None: ...

    def create_dataset(self, project: UUID, name: str, description: str) -> UUID:
        """Raises Conflict, naming the dataset that holds the name, when it's taken."""
        ...

    def open(self, project: UUID, dataset_id: UUID, lock: bool) -> AbstractContextManager[Tx]:
        """A transaction on one dataset; `lock` holds its row lock until the end. Raises
        NotFound when the project has no such dataset."""
        ...

    def unpinned(self, limit: int) -> list[tuple[UUID, str]]:
        """`(project, trace_id)` for source traces of items not yet marked pinned, oldest first."""
        ...

    def mark_pinned(self, project: UUID, trace_ids: list[str]) -> None:
        """Marks every item in the project linking to these traces as pinned."""
        ...


class Pins(Protocol):
    def pin(self, project: UUID, trace_ids: list[str]) -> None:
        """Exempts the traces from retention (ADR-0021). Returns once the change is queued."""
        ...


class TraceInputs(Protocol):
    def root_input(self, project: UUID, trace_id: str) -> Any:
        """The trace's root span input, in full. Raises NotFound or Refused."""
        ...


# --- Rules ----------------------------------------------------------------------------------


@contextmanager
def _writing(store: Store, project: UUID, dataset_id: UUID) -> Iterator[Tx]:
    """Every draft change and publish runs under the dataset's row lock, so a publish copies a
    draft nobody is changing (ADR-0020). Archived datasets are read-only."""
    with store.open(project, dataset_id, lock=True) as tx:
        if tx.dataset.archived:
            raise Refused("the dataset is archived; unarchive it to change it")
        yield tx


def _reload(tx: Tx, item_id: UUID) -> Item:
    item = tx.draft_item(item_id)
    assert item is not None, "an item just written is in the draft"
    return item


def _current(tx: Tx, item_id: UUID, base_rev: int) -> Item:
    """The draft item an edit is based on; a stale `base_rev` is a conflict."""
    item = tx.draft_item(item_id)
    if item is None:
        raise NotFound("no such item in the draft")
    if item.rev != base_rev:
        raise Conflict(
            f"the item changed since you loaded it (rev {base_rev}, now {item.rev}); "
            "reload and try again",
            rev=item.rev,
        )
    return item


def create_dataset(store: Store, project: UUID, name: str, description: str) -> Dataset:
    dataset_id = store.create_dataset(project, name, description.strip())
    return get_dataset(store, project, dataset_id)


def get_dataset(store: Store, project: UUID, dataset_id: UUID) -> Dataset:
    dataset = store.get_dataset(project, dataset_id)
    if dataset is None:
        raise NotFound("no such dataset")
    return dataset


def update_dataset(
    store: Store,
    project: UUID,
    dataset_id: UUID,
    name: str | None,
    description: str | None,
    archived: bool | None,
) -> Dataset:
    """Rename, describe, archive or unarchive. Allowed on archived datasets, so they can be
    unarchived."""
    with store.open(project, dataset_id, lock=True) as tx:
        tx.update(name, description.strip() if description is not None else None, archived)
    return get_dataset(store, project, dataset_id)


def add_item(store: Store, project: UUID, dataset_id: UUID, contents: Contents) -> Item:
    with _writing(store, project, dataset_id) as tx:
        item_id = tx.insert_item(None)
        tx.insert_revision(item_id, 1, contents)
        return _reload(tx, item_id)


def edit_item(
    store: Store, project: UUID, dataset_id: UUID, item_id: UUID, base_rev: int, contents: Contents
) -> Item:
    """Adds a revision. Published revisions never change."""
    with _writing(store, project, dataset_id) as tx:
        item = _current(tx, item_id, base_rev)
        tx.insert_revision(item_id, item.rev + 1, contents)
        return _reload(tx, item_id)


def delete_item(
    store: Store, project: UUID, dataset_id: UUID, item_id: UUID, base_rev: int
) -> None:
    """Drops the item from the next version. An item a version points at is kept."""
    with _writing(store, project, dataset_id) as tx:
        _current(tx, item_id, base_rev)
        tx.remove_item(item_id, hard=not tx.is_published(item_id))


def _trace_contents(traces: TraceInputs, project: UUID, trace_id: str) -> Contents:
    try:
        return Contents(input=traces.root_input(project, trace_id))
    except ValueError as e:
        raise Refused(f"the trace's input can't be copied: {e}") from e


def _insert_from_trace(tx: Tx, trace_id: str, contents: Contents) -> Item:
    existing = tx.item_for_trace(trace_id)
    if existing is not None:
        raise Conflict("this trace is already in the draft", item_id=existing)
    item_id = tx.insert_item(trace_id)
    tx.insert_revision(item_id, 1, contents)
    return _reload(tx, item_id)


def pin_traces(store: Store, pins: Pins, project: UUID, trace_ids: list[str]) -> bool:
    """Pins the traces and marks their items pinned. A failure is logged, not raised: the item
    holds its own copy of the input, and unmarked items are retried on startup (ADR-0021)."""
    try:
        pins.pin(project, trace_ids)
        store.mark_pinned(project, trace_ids)
    except Exception:
        log.warning(
            "could not pin %d traces; retrying on next startup", len(trace_ids), exc_info=True
        )
        return False
    return True


def pin_pending(store: Store, pins: Pins, batch: int = 500) -> int:
    """Pins every unmarked item's source trace: the backfill, and pins that failed earlier.
    Stops at the first failure, so a ClickHouse outage doesn't spin. Returns traces pinned."""
    pinned = 0
    while found := store.unpinned(batch):
        by_project: dict[UUID, list[str]] = defaultdict(list)
        for project, trace_id in found:
            by_project[project].append(trace_id)
        for project, trace_ids in by_project.items():
            if not pin_traces(store, pins, project, trace_ids):
                return pinned
            pinned += len(trace_ids)
    return pinned


def add_from_trace(
    store: Store, traces: TraceInputs, pins: Pins, project: UUID, dataset_id: UUID, trace_id: str
) -> Item:
    """Add to dataset (GLOSSARY.md): the server copies the root span's full input. The trace is
    read first, outside the transaction, then every check and the insert run under the lock. The
    trace is pinned once the item is committed."""
    contents = _trace_contents(traces, project, trace_id)
    with _writing(store, project, dataset_id) as tx:
        item = _insert_from_trace(tx, trace_id, contents)
    pin_traces(store, pins, project, [trace_id])
    return item


def create_from_trace(
    store: Store,
    traces: TraceInputs,
    pins: Pins,
    project: UUID,
    name: str,
    description: str,
    trace_id: str,
) -> Dataset:
    """A new dataset whose draft starts with the trace. The trace is read before the dataset is
    created, so a trace that can't be copied leaves no empty dataset behind."""
    contents = _trace_contents(traces, project, trace_id)
    dataset_id = store.create_dataset(project, name, description.strip())
    with _writing(store, project, dataset_id) as tx:
        _insert_from_trace(tx, trace_id, contents)
    pin_traces(store, pins, project, [trace_id])
    return get_dataset(store, project, dataset_id)


def _status(item: Item, latest: dict[UUID, int] | None) -> Status:
    if latest is None or item.item_id not in latest:
        return "new"
    return "unchanged" if latest[item.item_id] == item.rev else "changed"


def _draft(tx: Tx) -> tuple[Draft, dict[UUID, int]]:
    """The draft, each item marked against the latest version, and that version's pointers."""
    latest = tx.latest()
    pointers = latest[1] if latest else None
    entries = []
    for item in tx.draft():
        status = _status(item, pointers)
        ready = status == "unchanged" or item.contents.has_expectation()
        entries.append(DraftEntry(**dict(item), status=status, needs_expectation=not ready))
    return Draft(latest_version=latest[0] if latest else None, items=entries), pointers or {}


def draft(store: Store, project: UUID, dataset_id: UUID) -> Draft:
    with store.open(project, dataset_id, lock=False) as tx:
        return _draft(tx)[0]


def publish(store: Store, project: UUID, dataset_id: UUID) -> int:
    with _writing(store, project, dataset_id) as tx:
        current, latest = _draft(tx)
        entries = {e.item_id: e.rev for e in current.items}
        if entries == latest:
            raise Refused("nothing to publish: the draft equals the latest version")
        if not entries:
            raise Refused("the draft is empty")
        unready = [e.item_id for e in current.items if e.needs_expectation]
        if unready:
            raise Refused(
                f"{len(unready)} new or changed items have no expected output or "
                "expected trajectory",
                items=unready,
            )
        n = (current.latest_version or 0) + 1
        tx.insert_version(n, entries)
        return n


def versions(store: Store, project: UUID, dataset_id: UUID) -> list[VersionInfo]:
    with store.open(project, dataset_id, lock=False) as tx:
        return tx.versions()


def diff_version(store: Store, project: UUID, dataset_id: UUID, n: int) -> VersionDiff:
    """Version `n` against `n - 1`: what was added, changed, removed, and left unchanged."""
    with store.open(project, dataset_id, lock=False) as tx:
        found = tx.version(n)
        if found is None:
            raise NotFound(f"no version {n}")
        prev = tx.version(n - 1) if n > 1 else None
    info, items = found
    before = {i.item_id: i for i in prev[1]} if prev else {}

    def change(i: Item) -> Change:
        old = before.get(i.item_id)
        return "added" if old is None else "unchanged" if old.rev == i.rev else "changed"

    now = {i.item_id for i in items}
    return VersionDiff(
        **dict(info),
        items=[VersionEntry(**dict(i), change=change(i)) for i in items],
        removed=[i for i in before.values() if i.item_id not in now],
    )
