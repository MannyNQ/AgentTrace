"""Postgres storage for replays (schema: migrations/0004_replays.sql and 0006_forks.sql). It
reads and writes rows only; the decisions are in `replays`."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from trayce.eval.results import NewReplay, SpanRef
from trayce_ingest.dataset_store import ITEM_COLUMNS, item_from_row
from trayce_ingest.datasets import Conflict, Item, NotFound
from trayce_ingest.experiments import Status
from trayce_ingest.replays import PassCount, Replay, ReplayInfo, StoredReplayRun
from trayce_ingest.settings import Settings

REPLAY_COLUMNS = """r.id, r.original_trace_id, r.original_span_id, r.fork_trace_id,
    r.fork_span_id, r.tool_mode, r.status, r.error, r.runs, r.item_id, r.item_rev, r.scorers,
    r.config, r.config_hash, r.git_commit, r.git_branch, r.git_dirty, r.started_at,
    r.finished_at"""

# Listings add each replay's progress, cost, and pass counts per scorer.
INFO_SELECT = f"""
    SELECT {REPLAY_COLUMNS},
           s.uploaded, s.errored, s.cost, s.unpriced, s.judge_cost,
           coalesce((SELECT jsonb_object_agg(scorer, jsonb_build_array(passed, scored))
                     FROM (SELECT scorer, count(*) FILTER (WHERE passed) AS passed,
                                  count(*) AS scored
                           FROM replay_scores WHERE replay_id = r.id GROUP BY scorer) c),
                    '{{}}')
    FROM replays r
    LEFT JOIN LATERAL (
        SELECT count(*) AS uploaded, count(*) FILTER (WHERE error IS NOT NULL) AS errored,
               coalesce(sum(cost_usd), 0) AS cost,
               count(*) FILTER (WHERE cost_usd IS NULL) AS unpriced,
               coalesce(sum(judge_cost_usd), 0) AS judge_cost
        FROM replay_runs WHERE replay_id = r.id
    ) s ON true"""


def _fields(row: tuple) -> dict[str, Any]:
    (id_, trace, span, fork_trace, fork_span, tool_mode, status, error, runs, item_id, item_rev,
     scorers, config, config_hash, commit, branch, dirty, started, finished) = row  # fmt: skip
    return dict(
        id=id_,
        replay_of=SpanRef(trace_id=trace, span_id=span),
        fork_of=None if fork_trace is None else SpanRef(trace_id=fork_trace, span_id=fork_span),
        tool_mode=tool_mode,
        status=status,
        error=error,
        runs=runs,
        item_id=item_id,
        item_rev=item_rev,
        scorers=scorers,
        config=config,
        config_hash=config_hash,
        git_commit=commit,
        git_branch=branch,
        git_dirty=dirty,
        started_at=started,
        finished_at=finished,
    )


def _info(row: tuple) -> ReplayInfo:
    *replay, uploaded, errored, cost, unpriced, judge_cost, counts = row
    fields = _fields(tuple(replay))
    return ReplayInfo(
        **fields,
        runs_uploaded=uploaded,
        errored=errored,
        cost_usd=cost,
        unpriced_runs=unpriced,
        judge_cost_usd=judge_cost,
        pass_counts=[
            PassCount(scorer=s["name"], passed=counts[s["name"]][0], scored=counts[s["name"]][1])
            for s in fields["scorers"]
            if s["name"] in counts
        ],
    )


class PostgresTx:
    def __init__(self, conn: psycopg.Connection, replay: Replay):
        self.conn, self.replay = conn, replay

    def count_runs(self) -> int:
        row = self.conn.execute(
            "SELECT count(*) FROM replay_runs WHERE replay_id = %s", (self.replay.id,)
        ).fetchone()
        assert row is not None
        return row[0]

    def upsert_runs(self, runs: list[StoredReplayRun]) -> None:
        rid = self.replay.id
        with self.conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO replay_runs
                     (replay_id, run_index, trace_id, started_at, latency_ms, output,
                      output_truncated, trajectory, error, usage, cost_usd, judge_usage,
                      judge_cost_usd, diverged_at, warnings)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (replay_id, run_index) DO UPDATE SET
                     trace_id = EXCLUDED.trace_id, started_at = EXCLUDED.started_at,
                     latency_ms = EXCLUDED.latency_ms, output = EXCLUDED.output,
                     output_truncated = EXCLUDED.output_truncated,
                     trajectory = EXCLUDED.trajectory, error = EXCLUDED.error,
                     usage = EXCLUDED.usage, cost_usd = EXCLUDED.cost_usd,
                     judge_usage = EXCLUDED.judge_usage,
                     judge_cost_usd = EXCLUDED.judge_cost_usd,
                     diverged_at = EXCLUDED.diverged_at, warnings = EXCLUDED.warnings,
                     uploaded_at = now()""",
                [
                    (
                        rid,
                        r.run_index,
                        r.trace_id,
                        r.started_at,
                        r.latency_ms,
                        None if r.error is not None else Jsonb(r.output),
                        r.output_truncated,
                        Jsonb([t.model_dump(mode="json") for t in r.trajectory]),
                        r.error,
                        Jsonb([u.model_dump(mode="json") for u in r.usage]),
                        r.cost_usd,
                        Jsonb([u.model_dump(mode="json") for u in r.judge_usage]),
                        r.judge_cost_usd,
                        r.diverged_at,
                        Jsonb([w.model_dump(mode="json") for w in r.warnings]),
                    )
                    for r in runs
                ],
            )
            cur.executemany(
                "DELETE FROM replay_scores WHERE replay_id = %s AND run_index = %s",
                [(rid, r.run_index) for r in runs],
            )
            cur.executemany(
                """INSERT INTO replay_scores (replay_id, run_index, scorer, score, passed, reason)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                [
                    (rid, r.run_index, sc.scorer, sc.score, sc.passed, sc.reason)
                    for r in runs
                    for sc in r.scores
                ],
            )

    def finish(self, status: Status, error: str | None) -> None:
        self.conn.execute(
            "UPDATE replays SET status = %s, error = %s, finished_at = now() WHERE id = %s",
            (status, error, self.replay.id),
        )


class PostgresReplays:
    def __init__(self, s: Settings):
        self.dsn = s.postgres_dsn

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, connect_timeout=5)

    def item(self, project: UUID, item_id: UUID, rev: int) -> Item | None:
        with self._connect() as conn:
            row = conn.execute(
                f"""SELECT {ITEM_COLUMNS} FROM dataset_items i
                    JOIN item_revisions r ON r.item_id = i.item_id
                    JOIN datasets d ON d.id = i.dataset_id
                    WHERE d.project_id = %s AND i.item_id = %s AND r.rev = %s""",
                (project, item_id, rev),
            ).fetchone()
        return item_from_row(row) if row else None

    def create(self, project: UUID, r: NewReplay) -> None:
        with self._connect() as conn:
            try:
                conn.execute(
                    """INSERT INTO replays
                         (id, project_id, original_trace_id, original_span_id, fork_trace_id,
                          fork_span_id, tool_mode, runs, item_id, item_rev, scorers, config,
                          config_hash, git_commit, git_branch, git_dirty)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        r.id,
                        project,
                        r.replay_of.trace_id,
                        r.replay_of.span_id,
                        None if r.fork_of is None else r.fork_of.trace_id,
                        None if r.fork_of is None else r.fork_of.span_id,
                        r.tool_mode,
                        r.runs,
                        r.item_id,
                        r.item_rev,
                        Jsonb([s.model_dump(mode="json") for s in r.scorers]),
                        None if r.config is None else Jsonb(r.config),
                        r.config_hash,
                        r.git_commit,
                        r.git_branch,
                        r.git_dirty,
                    ),
                )
            except psycopg.errors.UniqueViolation as e:
                raise Conflict("a replay with this id already exists") from e

    def get(self, project: UUID, replay_id: UUID) -> ReplayInfo | None:
        with self._connect() as conn:
            row = conn.execute(
                f"{INFO_SELECT} WHERE r.id = %s AND r.project_id = %s", (replay_id, project)
            ).fetchone()
        return _info(row) if row else None

    def list_for_trace(self, project: UUID, trace_id: str) -> list[ReplayInfo]:
        with self._connect() as conn:
            rows = conn.execute(
                f"""{INFO_SELECT} WHERE r.project_id = %s
                      AND (r.original_trace_id = %s OR r.fork_trace_id = %s)
                    ORDER BY r.started_at DESC, r.id""",
                (project, trace_id, trace_id),
            ).fetchall()
        return [_info(r) for r in rows]

    @contextmanager
    def open(self, project: UUID, replay_id: UUID, exclusive: bool) -> Iterator[PostgresTx]:
        with self._connect() as conn, conn.transaction():
            row = conn.execute(
                f"""SELECT {REPLAY_COLUMNS} FROM replays r
                    WHERE r.id = %s AND r.project_id = %s
                    {"FOR UPDATE" if exclusive else "FOR SHARE"}""",
                (replay_id, project),
            ).fetchone()
            if row is None:
                raise NotFound("no such replay")
            yield PostgresTx(conn, Replay(**_fields(row)))

    def runs(self, replay_id: UUID) -> list[StoredReplayRun]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT u.run_index, u.trace_id, u.started_at, u.latency_ms, u.output,
                          u.output_truncated, u.trajectory, u.error, u.usage, u.cost_usd,
                          u.judge_usage, u.judge_cost_usd, u.diverged_at, u.warnings,
                          coalesce(jsonb_agg(jsonb_build_object(
                              'scorer', c.scorer, 'score', c.score, 'passed', c.passed,
                              'reason', c.reason) ORDER BY c.scorer)
                            FILTER (WHERE c.scorer IS NOT NULL), '[]')
                   FROM replay_runs u
                   LEFT JOIN replay_scores c USING (replay_id, run_index)
                   WHERE u.replay_id = %s
                   GROUP BY u.replay_id, u.run_index
                   ORDER BY u.run_index""",
                (replay_id,),
            ).fetchall()
        return [
            StoredReplayRun(
                run_index=n,
                trace_id=trace,
                started_at=started,
                latency_ms=ms,
                output=output,
                output_truncated=truncated,
                trajectory=trajectory,
                error=error,
                usage=usage,
                cost_usd=cost,
                judge_usage=judge_usage,
                judge_cost_usd=judge_cost,
                diverged_at=diverged_at,
                warnings=warnings,
                scores=scores,
            )
            for (
                n,
                trace,
                started,
                ms,
                output,
                truncated,
                trajectory,
                error,
                usage,
                cost,
                judge_usage,
                judge_cost,
                diverged_at,
                warnings,
                scores,
            ) in rows  # fmt: skip
        ]
