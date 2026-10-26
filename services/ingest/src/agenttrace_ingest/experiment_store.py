"""Postgres storage for experiments (schema: migrations/0002_experiments.sql). It reads and writes
rows only; the decisions are in `experiments`."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from agenttrace.evals import NewExperiment
from agenttrace.pricing import PRICES_SQL, PriceTable
from agenttrace_ingest.dataset_store import ITEM_COLUMNS, item_from_row
from agenttrace_ingest.datasets import Conflict, Item, NotFound
from agenttrace_ingest.experiments import (
    Experiment,
    ExperimentInfo,
    SampleRow,
    ScoreRow,
    Status,
    StoredSample,
)
from agenttrace_ingest.settings import Settings

EXPERIMENT_COLUMNS = """e.id, e.dataset_id, e.dataset_version, e.name, e.status, e.error,
    e.samples_per_item, e.scorers, e.config, e.config_hash, e.git_commit, e.git_branch,
    e.started_at, e.finished_at"""

# Listings add each experiment's item count, progress, and cost.
INFO_SELECT = f"""
    SELECT {EXPERIMENT_COLUMNS},
           (SELECT count(*) FROM version_items v
            WHERE v.dataset_id = e.dataset_id AND v.version = e.dataset_version),
           s.uploaded, s.cost, s.unpriced
    FROM experiments e
    LEFT JOIN LATERAL (
        SELECT count(*) AS uploaded, coalesce(sum(cost_usd), 0) AS cost,
               count(*) FILTER (WHERE cost_usd IS NULL) AS unpriced
        FROM experiment_samples WHERE experiment_id = e.id
    ) s ON true"""


def _fields(row: tuple) -> dict[str, Any]:
    (id_, dataset_id, version, name, status, error, samples, scorers, config, config_hash,
     commit, branch, started, finished) = row  # fmt: skip
    return dict(
        id=id_,
        dataset_id=dataset_id,
        dataset_version=version,
        name=name,
        status=status,
        error=error,
        samples=samples,
        scorers=scorers,
        config=config,
        config_hash=config_hash,
        git_commit=commit,
        git_branch=branch,
        started_at=started,
        finished_at=finished,
    )


def _info(row: tuple) -> ExperimentInfo:
    *experiment, items, uploaded, cost, unpriced = row
    return ExperimentInfo(
        **_fields(tuple(experiment)),
        item_count=items,
        samples_uploaded=uploaded,
        cost_usd=cost,
        unpriced_samples=unpriced,
    )


def _version_items(
    conn: psycopg.Connection, project: UUID, dataset_id: UUID, version: int
) -> dict[UUID, int] | None:
    found = conn.execute(
        """SELECT 1 FROM dataset_versions v JOIN datasets d ON d.id = v.dataset_id
           WHERE d.project_id = %s AND v.dataset_id = %s AND v.version = %s""",
        (project, dataset_id, version),
    ).fetchone()
    if found is None:
        return None
    rows = conn.execute(  # in dataset order, as the version page lists them
        """SELECT v.item_id, v.rev FROM version_items v
           JOIN dataset_items i ON i.item_id = v.item_id
           WHERE v.dataset_id = %s AND v.version = %s ORDER BY i.created_at, i.item_id""",
        (dataset_id, version),
    ).fetchall()
    return dict(rows)


def _sample(item_id: UUID, row: tuple) -> StoredSample:
    n, trace, started, ms, output, truncated, trajectory, error, usage, cost, scores = row
    return StoredSample(
        item_id=item_id,
        sample_index=n,
        trace_id=trace,
        started_at=started,
        latency_ms=ms,
        output=output,
        output_truncated=truncated,
        trajectory=trajectory,
        error=error,
        usage=usage,
        cost_usd=cost,
        scores=scores,
    )


class PostgresTx:
    def __init__(self, conn: psycopg.Connection, project: UUID, experiment: Experiment):
        self.conn, self.project, self.experiment = conn, project, experiment

    def version_items(self) -> dict[UUID, int]:
        e = self.experiment
        return _version_items(self.conn, self.project, e.dataset_id, e.dataset_version) or {}

    def count_samples(self) -> int:
        row = self.conn.execute(
            "SELECT count(*) FROM experiment_samples WHERE experiment_id = %s",
            (self.experiment.id,),
        ).fetchone()
        assert row is not None
        return row[0]

    def upsert_samples(self, samples: list[StoredSample]) -> None:
        eid = self.experiment.id
        with self.conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO experiment_samples
                     (experiment_id, item_id, sample_index, trace_id, started_at, latency_ms,
                      output, output_truncated, trajectory, error, usage, cost_usd)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (experiment_id, item_id, sample_index) DO UPDATE SET
                     trace_id = EXCLUDED.trace_id, started_at = EXCLUDED.started_at,
                     latency_ms = EXCLUDED.latency_ms, output = EXCLUDED.output,
                     output_truncated = EXCLUDED.output_truncated,
                     trajectory = EXCLUDED.trajectory, error = EXCLUDED.error,
                     usage = EXCLUDED.usage, cost_usd = EXCLUDED.cost_usd,
                     uploaded_at = now()""",
                [
                    (
                        eid,
                        s.item_id,
                        s.sample_index,
                        s.trace_id,
                        s.started_at,
                        s.latency_ms,
                        None if s.error is not None else Jsonb(s.output),
                        s.output_truncated,
                        Jsonb([t.model_dump(mode="json") for t in s.trajectory]),
                        s.error,
                        Jsonb([u.model_dump(mode="json") for u in s.usage]),
                        s.cost_usd,
                    )
                    for s in samples
                ],
            )
            cur.executemany(
                """DELETE FROM experiment_scores
                   WHERE experiment_id = %s AND item_id = %s AND sample_index = %s""",
                [(eid, s.item_id, s.sample_index) for s in samples],
            )
            cur.executemany(
                """INSERT INTO experiment_scores
                     (experiment_id, item_id, sample_index, scorer, score, passed, reason)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                [
                    (eid, s.item_id, s.sample_index, sc.scorer, sc.score, sc.passed, sc.reason)
                    for s in samples
                    for sc in s.scores
                ],
            )

    def finish(self, status: Status, error: str | None) -> None:
        self.conn.execute(
            "UPDATE experiments SET status = %s, error = %s, finished_at = now() WHERE id = %s",
            (status, error, self.experiment.id),
        )


class PostgresExperiments:
    def __init__(self, s: Settings):
        self.dsn = s.postgres_dsn

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, connect_timeout=5)

    def version_items(
        self, project: UUID, dataset_id: UUID, version: int
    ) -> dict[UUID, int] | None:
        with self._connect() as conn:
            return _version_items(conn, project, dataset_id, version)

    def create(self, project: UUID, e: NewExperiment) -> None:
        with self._connect() as conn:
            try:
                conn.execute(
                    """INSERT INTO experiments
                         (id, project_id, dataset_id, dataset_version, name, samples_per_item,
                          scorers, config, config_hash, git_commit, git_branch)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        e.id,
                        project,
                        e.dataset_id,
                        e.dataset_version,
                        e.name,
                        e.samples,
                        Jsonb([s.model_dump(mode="json") for s in e.scorers]),
                        Jsonb(e.config),
                        e.config_hash,
                        e.git_commit,
                        e.git_branch,
                    ),
                )
            except psycopg.errors.UniqueViolation as e_:
                raise Conflict("an experiment with this id already exists") from e_

    def get(self, project: UUID, experiment_id: UUID) -> ExperimentInfo | None:
        with self._connect() as conn:
            row = conn.execute(
                f"{INFO_SELECT} WHERE e.id = %s AND e.project_id = %s", (experiment_id, project)
            ).fetchone()
        return _info(row) if row else None

    def list_experiments(self, project: UUID, dataset_id: UUID | None) -> list[ExperimentInfo]:
        with self._connect() as conn:
            rows = conn.execute(
                f"""{INFO_SELECT}
                    WHERE e.project_id = %s AND (%s::uuid IS NULL OR e.dataset_id = %s::uuid)
                    ORDER BY e.started_at DESC, e.id""",
                (project, dataset_id, dataset_id),
            ).fetchall()
        return [_info(r) for r in rows]

    @contextmanager
    def open(self, project: UUID, experiment_id: UUID, exclusive: bool) -> Iterator[PostgresTx]:
        with self._connect() as conn, conn.transaction():
            row = conn.execute(
                f"""SELECT {EXPERIMENT_COLUMNS} FROM experiments e
                    WHERE e.id = %s AND e.project_id = %s
                    {"FOR UPDATE" if exclusive else "FOR SHARE"}""",
                (experiment_id, project),
            ).fetchone()
            if row is None:
                raise NotFound("no such experiment")
            yield PostgresTx(conn, project, Experiment(**_fields(row)))

    def sample_rows(self, experiment_id: UUID) -> list[SampleRow]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT item_id, sample_index, error IS NOT NULL, latency_ms
                   FROM experiment_samples WHERE experiment_id = %s""",
                (experiment_id,),
            ).fetchall()
        return [
            SampleRow(item_id=i, sample_index=n, errored=err, latency_ms=ms)
            for i, n, err, ms in rows
        ]

    def score_rows(self, experiment_id: UUID) -> list[ScoreRow]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT item_id, sample_index, scorer, score, passed
                   FROM experiment_scores WHERE experiment_id = %s""",
                (experiment_id,),
            ).fetchall()
        return [
            ScoreRow(item_id=i, sample_index=n, scorer=sc, score=score, passed=passed)
            for i, n, sc, score, passed in rows
        ]

    def samples(self, experiment_id: UUID, item_id: UUID) -> list[StoredSample]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT s.sample_index, s.trace_id, s.started_at, s.latency_ms, s.output,
                          s.output_truncated, s.trajectory, s.error, s.usage, s.cost_usd,
                          coalesce(jsonb_agg(jsonb_build_object(
                              'scorer', c.scorer, 'score', c.score, 'passed', c.passed,
                              'reason', c.reason) ORDER BY c.scorer)
                            FILTER (WHERE c.scorer IS NOT NULL), '[]')
                   FROM experiment_samples s
                   LEFT JOIN experiment_scores c USING (experiment_id, item_id, sample_index)
                   WHERE s.experiment_id = %s AND s.item_id = %s
                   GROUP BY s.experiment_id, s.item_id, s.sample_index
                   ORDER BY s.sample_index""",
                (experiment_id, item_id),
            ).fetchall()
        return [_sample(item_id, r) for r in rows]

    def item(self, item_id: UUID, rev: int) -> Item:
        with self._connect() as conn:
            row = conn.execute(
                f"""SELECT {ITEM_COLUMNS} FROM dataset_items i
                    JOIN item_revisions r ON r.item_id = i.item_id
                    WHERE i.item_id = %s AND r.rev = %s""",
                (item_id, rev),
            ).fetchone()
        assert row is not None, "a version's revision is never deleted (ADR-0020)"
        return item_from_row(row)


class PostgresPrices:
    """The price table, reloaded at most once a minute like the worker's."""

    def __init__(self, s: Settings, ttl_s: float = 60.0):
        self.dsn, self.ttl_s = s.postgres_dsn, ttl_s
        self._table: PriceTable | None = None
        self._loaded_at = 0.0

    def get(self) -> PriceTable:
        if self._table is None or time.monotonic() - self._loaded_at > self.ttl_s:
            with psycopg.connect(self.dsn, connect_timeout=5) as conn:
                self._table = PriceTable.from_rows(conn.execute(PRICES_SQL).fetchall())
            self._loaded_at = time.monotonic()
        return self._table
