"""Which project a trace, dataset, experiment, or replay belongs to, among the projects of the
caller's org. The web app uses it to send a link from before projects were in URLs
(`/replays/<id>`) to `/p/<slug>/replays/<id>` (design.md, Phase 6)."""

import re
from functools import cached_property
from typing import Literal, Protocol
from uuid import UUID

import psycopg
from psycopg import sql

from agenttrace_ingest.auth import Store as AuthStore
from agenttrace_ingest.auth import User
from agenttrace_ingest.datasets import NotFound, Record
from agenttrace_ingest.settings import Settings
from agenttrace_ingest.stores import clickhouse_client
from agenttrace_ingest.trace_store import clickhouse_reads

Kind = Literal["trace", "dataset", "experiment", "replay"]
TABLES: dict[str, str] = {"dataset": "datasets", "experiment": "experiments", "replay": "replays"}


class Located(Record):
    slug: str


class Store(Protocol):
    def find(self, kind: Kind, entity_id: str, projects: list[UUID]) -> UUID | None:
        """The project among `projects` holding the entity, if any."""
        ...


def locate(auth: AuthStore, store: Store, user: User, kind: Kind, entity_id: str) -> Located:
    """Not found and in another org look alike, and so does a malformed id."""
    if not _well_formed(kind, entity_id):
        raise NotFound(f"{kind} not found")
    projects = {p.id: p.slug for p in auth.projects(user.org_id)}
    found = store.find(kind, entity_id, list(projects))
    if found is None:
        raise NotFound(f"{kind} not found")
    return Located(slug=projects[found])


def _well_formed(kind: Kind, entity_id: str) -> bool:
    if kind == "trace":
        return re.fullmatch(r"[0-9a-f]{32}", entity_id) is not None
    try:
        UUID(entity_id)
    except ValueError:
        return False
    return True


class StoredLocations:
    def __init__(self, s: Settings):
        self.s = s

    @cached_property
    def _clickhouse(self):
        return clickhouse_client(self.s)

    def find(self, kind: Kind, entity_id: str, projects: list[UUID]) -> UUID | None:
        if not projects:
            return None
        if kind == "trace":
            # (project_id, trace_id) is the traces sort key, so this reads one granule per project.
            with clickhouse_reads():
                rows = self._clickhouse.query(
                    """SELECT project_id FROM traces
                       WHERE project_id IN {projects:Array(UUID)}
                         AND trace_id = {trace:FixedString(32)} LIMIT 1""",
                    parameters={"projects": [str(p) for p in projects], "trace": entity_id},
                ).result_rows
            return UUID(str(rows[0][0])) if rows else None
        with psycopg.connect(self.s.postgres_dsn, connect_timeout=5) as conn:
            row = conn.execute(
                sql.SQL("SELECT project_id FROM {} WHERE id = %s AND project_id = ANY(%s)").format(
                    sql.Identifier(TABLES[kind])
                ),
                (UUID(entity_id), projects),
            ).fetchone()
        return row[0] if row else None
