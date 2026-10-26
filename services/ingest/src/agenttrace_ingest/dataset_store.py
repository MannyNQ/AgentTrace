"""Postgres storage for datasets (schema: migrations/0001_datasets.sql). It reads and writes
rows only; the decisions are in `datasets`."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from agenttrace_ingest.datasets import (
    Conflict,
    Contents,
    Dataset,
    DatasetRow,
    Item,
    NotFound,
    VersionInfo,
)
from agenttrace_ingest.settings import Settings

# Listings add each dataset's latest version and live item count.
DATASET_SELECT = """
    SELECT d.id, d.name, d.description, d.archived_at IS NOT NULL, d.created_at,
           (SELECT max(version) FROM dataset_versions v WHERE v.dataset_id = d.id),
           (SELECT count(*) FROM dataset_items i
            WHERE i.dataset_id = d.id AND i.deleted_at IS NULL)
    FROM datasets d"""

# An item joined to one of its revisions, aliased i and r.
ITEM_COLUMNS = """i.item_id, r.rev, i.source_trace_id, i.created_at,
                  r.input, r.expected_output, r.expected_trajectory, r.metadata"""


def _dataset(row: tuple) -> Dataset:
    id_, name, description, archived, created_at, latest, items = row
    return Dataset(
        id=id_,
        name=name,
        description=description,
        archived=archived,
        created_at=created_at,
        latest_version=latest,
        draft_items=items,
    )


def item_from_row(row: tuple) -> Item:
    item_id, rev, source, created, inp, out, trajectory, metadata = row
    contents = Contents.model_validate(
        {
            "input": inp,
            "expected_output": out,
            "expected_trajectory": trajectory,
            "metadata": metadata,
        }
    )
    return Item(
        item_id=item_id, rev=rev, source_trace_id=source, created_at=created, contents=contents
    )


def _version(row: tuple) -> VersionInfo:
    version, item_count, published_at = row
    return VersionInfo(version=version, item_count=item_count, published_at=published_at)


def _jsonb(v: Any) -> Jsonb | None:
    return None if v is None else Jsonb(v)


def _name_taken(conn: psycopg.Connection, project: UUID, name: str | None) -> Conflict:
    """The conflict names the dataset holding the name, which may be archived and so hidden."""
    row = conn.execute(
        "SELECT id FROM datasets WHERE project_id = %s AND name = %s", (project, name)
    ).fetchone()
    return Conflict(f'a dataset named "{name}" already exists', dataset_id=row[0] if row else None)


class PostgresTx:
    def __init__(self, conn: psycopg.Connection, project: UUID, dataset: DatasetRow):
        self.conn, self.project, self.dataset = conn, project, dataset

    @property
    def _id(self) -> UUID:
        return self.dataset.id

    def update(self, name: str | None, description: str | None, archived: bool | None) -> None:
        try:
            with self.conn.transaction():  # a savepoint, so the name's holder can be looked up
                self.conn.execute(
                    """UPDATE datasets SET
                     name = coalesce(%s::text, name),
                     description = coalesce(%s::text, description),
                     archived_at = CASE WHEN %s::boolean IS NULL THEN archived_at
                                        WHEN %s::boolean THEN coalesce(archived_at, now())
                                        ELSE NULL END
                   WHERE id = %s""",
                    (name, description, archived, archived, self._id),
                )
        except psycopg.errors.UniqueViolation as e:
            raise _name_taken(self.conn, self.project, name) from e

    def draft(self) -> list[Item]:
        rows = self.conn.execute(
            f"""SELECT {ITEM_COLUMNS} FROM dataset_items i
                JOIN item_revisions r ON r.item_id = i.item_id AND r.rev = i.current_rev
                WHERE i.dataset_id = %s AND i.deleted_at IS NULL
                ORDER BY i.created_at, i.item_id""",
            (self._id,),
        ).fetchall()
        return [item_from_row(r) for r in rows]

    def draft_item(self, item_id: UUID) -> Item | None:
        row = self.conn.execute(
            f"""SELECT {ITEM_COLUMNS} FROM dataset_items i
                JOIN item_revisions r ON r.item_id = i.item_id AND r.rev = i.current_rev
                WHERE i.dataset_id = %s AND i.item_id = %s AND i.deleted_at IS NULL""",
            (self._id, item_id),
        ).fetchone()
        return item_from_row(row) if row else None

    def item_for_trace(self, trace_id: str) -> UUID | None:
        row = self.conn.execute(
            """SELECT item_id FROM dataset_items
               WHERE dataset_id = %s AND source_trace_id = %s AND deleted_at IS NULL""",
            (self._id, trace_id),
        ).fetchone()
        return row[0] if row else None

    def latest(self) -> tuple[int, dict[UUID, int]] | None:
        row = self.conn.execute(
            "SELECT max(version) FROM dataset_versions WHERE dataset_id = %s", (self._id,)
        ).fetchone()
        if row is None or row[0] is None:
            return None
        rows = self.conn.execute(
            "SELECT item_id, rev FROM version_items WHERE dataset_id = %s AND version = %s",
            (self._id, row[0]),
        ).fetchall()
        return row[0], dict(rows)

    def versions(self) -> list[VersionInfo]:
        rows = self.conn.execute(
            """SELECT version, item_count, published_at FROM dataset_versions
               WHERE dataset_id = %s ORDER BY version DESC""",
            (self._id,),
        ).fetchall()
        return [_version(r) for r in rows]

    def version(self, n: int) -> tuple[VersionInfo, list[Item]] | None:
        info = self.conn.execute(
            """SELECT version, item_count, published_at FROM dataset_versions
               WHERE dataset_id = %s AND version = %s""",
            (self._id, n),
        ).fetchone()
        if info is None:
            return None
        rows = self.conn.execute(
            f"""SELECT {ITEM_COLUMNS} FROM version_items v
                JOIN dataset_items i ON i.item_id = v.item_id
                JOIN item_revisions r ON r.item_id = v.item_id AND r.rev = v.rev
                WHERE v.dataset_id = %s AND v.version = %s
                ORDER BY i.created_at, i.item_id""",
            (self._id, n),
        ).fetchall()
        return _version(info), [item_from_row(r) for r in rows]

    def insert_item(self, source_trace_id: str | None) -> UUID:
        row = self.conn.execute(
            """INSERT INTO dataset_items (dataset_id, current_rev, source_trace_id)
               VALUES (%s, 0, %s) RETURNING item_id""",
            (self._id, source_trace_id),
        ).fetchone()
        assert row is not None
        return row[0]

    def insert_revision(self, item_id: UUID, rev: int, contents: Contents) -> None:
        c = contents.model_dump(mode="json")
        self.conn.execute(
            """INSERT INTO item_revisions
                 (item_id, rev, input, expected_output, expected_trajectory, metadata)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                item_id,
                rev,
                Jsonb(c["input"]),
                _jsonb(c["expected_output"]),
                _jsonb(c["expected_trajectory"]),
                Jsonb(c["metadata"]),
            ),
        )
        self.conn.execute(
            "UPDATE dataset_items SET current_rev = %s WHERE item_id = %s AND dataset_id = %s",
            (rev, item_id, self._id),
        )

    def is_published(self, item_id: UUID) -> bool:
        row = self.conn.execute(
            "SELECT EXISTS (SELECT 1 FROM version_items WHERE item_id = %s)", (item_id,)
        ).fetchone()
        return bool(row and row[0])

    def remove_item(self, item_id: UUID, hard: bool) -> None:
        sql = (
            "DELETE FROM dataset_items"  # revisions cascade
            if hard
            else "UPDATE dataset_items SET deleted_at = now()"
        )
        self.conn.execute(f"{sql} WHERE item_id = %s AND dataset_id = %s", (item_id, self._id))

    def insert_version(self, n: int, entries: dict[UUID, int]) -> None:
        self.conn.execute(
            "INSERT INTO dataset_versions (dataset_id, version, item_count) VALUES (%s, %s, %s)",
            (self._id, n, len(entries)),
        )
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO version_items (dataset_id, version, item_id, rev) "
                "VALUES (%s, %s, %s, %s)",
                [(self._id, n, item_id, rev) for item_id, rev in entries.items()],
            )


class PostgresDatasets:
    def __init__(self, s: Settings):
        self.dsn = s.postgres_dsn

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, connect_timeout=5)

    def list_datasets(self, project: UUID, include_archived: bool) -> list[Dataset]:
        with self._connect() as conn:
            rows = conn.execute(
                f"""{DATASET_SELECT}
                    WHERE d.project_id = %s AND (%s OR d.archived_at IS NULL)
                    ORDER BY d.name""",
                (project, include_archived),
            ).fetchall()
        return [_dataset(r) for r in rows]

    def get_dataset(self, project: UUID, dataset_id: UUID) -> Dataset | None:
        with self._connect() as conn:
            row = conn.execute(
                f"{DATASET_SELECT} WHERE d.id = %s AND d.project_id = %s", (dataset_id, project)
            ).fetchone()
        return _dataset(row) if row else None

    def create_dataset(self, project: UUID, name: str, description: str) -> UUID:
        with self._connect() as conn:
            try:
                row = conn.execute(
                    """INSERT INTO datasets (project_id, name, description)
                       VALUES (%s, %s, %s) RETURNING id""",
                    (project, name, description),
                ).fetchone()
            except psycopg.errors.UniqueViolation as e:
                conn.rollback()
                raise _name_taken(conn, project, name) from e
        assert row is not None
        return row[0]

    def unpinned(self, limit: int) -> list[tuple[UUID, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT d.project_id, i.source_trace_id FROM dataset_items i
                   JOIN datasets d ON d.id = i.dataset_id
                   WHERE i.source_trace_id IS NOT NULL AND i.pinned_at IS NULL
                   GROUP BY d.project_id, i.source_trace_id
                   ORDER BY min(i.created_at) LIMIT %s""",
                (limit,),
            ).fetchall()
        return [(project, trace_id) for project, trace_id in rows]

    def mark_pinned(self, project: UUID, trace_ids: list[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE dataset_items i SET pinned_at = now() FROM datasets d
                   WHERE d.id = i.dataset_id AND d.project_id = %s
                     AND i.source_trace_id = ANY(%s) AND i.pinned_at IS NULL""",
                (project, trace_ids),
            )

    @contextmanager
    def open(self, project: UUID, dataset_id: UUID, lock: bool) -> Iterator[PostgresTx]:
        with self._connect() as conn, conn.transaction():
            row = conn.execute(
                f"""SELECT id, name, description, archived_at IS NOT NULL, created_at
                    FROM datasets WHERE id = %s AND project_id = %s
                    {"FOR UPDATE" if lock else ""}""",
                (dataset_id, project),
            ).fetchone()
            if row is None:
                raise NotFound("no such dataset")
            id_, name, description, archived, created_at = row
            dataset = DatasetRow(
                id=id_, name=name, description=description, archived=archived, created_at=created_at
            )
            yield PostgresTx(conn, project, dataset)
