"""Ordered schema migrations, applied on startup (design.md, Phase 2).

Each `migrations/NNNN_name.sql` runs once, in its own transaction, and is recorded in
`schema_migrations`. An advisory lock keeps two ingest processes from applying the same file.
The Phase 0 and 1 tables come from `infra/postgres` init SQL, which runs before ingest starts.
"""

import re
from importlib import resources

import psycopg

LOCK_ID = 0x61677472  # "agtr": any constant shared by every ingest process
FILE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def pending(applied: set[int]) -> list[tuple[int, str, str]]:
    """(version, name, sql) for every migration file not yet applied, in order."""
    files = []
    for f in resources.files("agenttrace_ingest.migrations").iterdir():
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
