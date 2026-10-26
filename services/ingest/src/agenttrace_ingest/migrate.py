"""Ordered schema migrations, applied on startup (design.md, Phases 2 and 5).

Each `migrations/NNNN_name.sql` runs once, in its own transaction, and is recorded in
`schema_migrations`. An advisory lock keeps two ingest processes from applying the same file.
The Phase 0 and 1 tables come from `infra/postgres` init SQL, which runs before ingest starts.

ClickHouse has its own series, `migrations/clickhouse/NNNN_name.sql`, recorded in a ClickHouse
`schema_migrations` table, under a second Postgres advisory lock. ClickHouse has no transactions,
so each statement must be safe to run again (`IF NOT EXISTS`, `MODIFY QUERY`): a file that stops
halfway is rerun whole on the next start. Its tables come from `infra/clickhouse` init SQL.
"""

import re
from importlib import resources

import psycopg

LOCK_ID = 0x61677472  # "agtr": any constant shared by every ingest process
CLICKHOUSE_LOCK_ID = 0x61677463  # "agtc"
FILE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
STATEMENT_END = re.compile(r";\s*$", re.MULTILINE)


def pending(
    applied: set[int], package: str = "agenttrace_ingest.migrations"
) -> list[tuple[int, str, str]]:
    """(version, name, sql) for every migration file not yet applied, in order."""
    files = []
    for f in resources.files(package).iterdir():
        m = FILE.match(f.name)
        if m and int(m[1]) not in applied:
            files.append((int(m[1]), f.name, f.read_text()))
    versions = [v for v, _, _ in files]
    if len(versions) != len(set(versions)):
        raise RuntimeError(f"duplicate migration numbers: {sorted(versions)}")
    return sorted(files)


def migrate(dsn: str) -> list[str]:
    """Apply pending migrations; returns the names applied."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version INT PRIMARY KEY, name TEXT NOT NULL,"
                " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
            done = []
            for version, name, sql in pending(applied):
                with conn.transaction():
                    conn.execute(sql.encode())  # bytes: run as a script, no parameter parsing
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                        (version, name),
                    )
                done.append(name)
            return done
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))


def statements(sql: str) -> list[str]:
    """A ClickHouse migration's statements: each ends with `;` at the end of a line, since the
    HTTP interface runs one statement per request."""
    return [s.strip() for s in STATEMENT_END.split(sql) if s.strip()]


def migrate_clickhouse(dsn: str, clickhouse) -> list[str]:
    """Apply pending ClickHouse migrations; returns the names applied. `clickhouse` is a
    clickhouse_connect client. The lock is taken in Postgres, which every ingest shares."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (CLICKHOUSE_LOCK_ID,))
        try:
            clickhouse.command(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version UInt32, name String, applied_at DateTime DEFAULT now())"
                " ENGINE = MergeTree ORDER BY version"
            )
            rows = clickhouse.query("SELECT version FROM schema_migrations").result_rows
            applied = {r[0] for r in rows}
            done = []
            for version, name, sql in pending(applied, "agenttrace_ingest.migrations.clickhouse"):
                for statement in statements(sql):
                    clickhouse.command(statement)
                clickhouse.insert("schema_migrations", [[version, name]], ["version", "name"])
                done.append(name)
            return done
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (CLICKHOUSE_LOCK_ID,))
