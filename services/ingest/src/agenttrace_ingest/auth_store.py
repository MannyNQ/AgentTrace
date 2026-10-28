"""Postgres storage for users, sessions, links, setup tokens, and API keys (schema:
migrations/0005_auth.sql), and Redis counters for failed logins. Rows only; the decisions are in
`auth`, apart from the atomic ones its `Store` names."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

import psycopg
import redis
from psycopg.rows import DictRow, dict_row

from agenttrace_ingest.auth import (
    LOGIN_FAILURE_WINDOW_S,
    KeyInfo,
    KeyRow,
    Link,
    LinkKind,
    Project,
    Role,
    Scope,
    User,
)
from agenttrace_ingest.datasets import Conflict, NotFound
from agenttrace_ingest.settings import Settings
from agenttrace_ingest.traces import Unavailable

USER_COLUMNS = """u.id, u.org_id, u.email, u.role, u.created_at,
                  u.deactivated_at IS NOT NULL AS deactivated"""
KEY_COLUMNS = """k.id, k.project_id, k.name, k.scope, k.hint, k.from_env, k.created_by,
                 k.created_at, k.last_used_at, k.revoked_at"""
PROJECT_COLUMNS = "id, org_id, slug, name, retention_days, created_at"
CLAIM_LOCK_ID = 0x61677475  # "agtu": serializes claiming the install
TAKEN = "a user with this email already exists"


class PostgresAuth:
    def __init__(self, s: Settings):
        self.dsn = s.postgres_dsn

    def _connect(self) -> psycopg.Connection[DictRow]:
        return psycopg.Connection[DictRow].connect(
            self.dsn, connect_timeout=5, row_factory=dict_row
        )

    # API keys

    def key(self, key_hash: str) -> KeyRow | None:
        with self._connect() as conn:
            row = conn.execute(
                f"""SELECT {KEY_COLUMNS}, p.org_id, p.slug, p.name AS project_name,
                           p.retention_days, p.created_at AS project_created_at
                    FROM api_keys k JOIN projects p ON p.id = k.project_id
                    WHERE k.key_hash = %s""",
                (key_hash,),
            ).fetchone()
        if row is None:
            return None
        project = Project(
            id=row["project_id"],
            org_id=row["org_id"],
            slug=row["slug"],
            name=row["project_name"],
            retention_days=row["retention_days"],
            created_at=row["project_created_at"],
        )
        return KeyRow(info=KeyInfo.model_validate(row), project=project)

    def touch_key(self, key_id: UUID) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE api_keys SET last_used_at = now() WHERE id = %s", (key_id,))

    def register_env_key(self, key_hash: str, hint: str, project_id: UUID) -> None:
        with self._connect() as conn, conn.transaction():
            conn.execute(
                "UPDATE api_keys SET from_env = false WHERE from_env AND key_hash <> %s",
                (key_hash,),
            )
            conn.execute(
                """INSERT INTO api_keys (key_hash, project_id, name, scope, hint, from_env)
                   VALUES (%s, %s, '.env', 'full', %s, true)
                   ON CONFLICT (key_hash) DO UPDATE SET
                     project_id = EXCLUDED.project_id, scope = 'full', hint = EXCLUDED.hint,
                     from_env = true, revoked_at = NULL,
                     name = CASE WHEN api_keys.name = '' THEN '.env' ELSE api_keys.name END""",
                (key_hash, project_id, hint),
            )

    def revoke_key_hash(self, key_hash: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE api_keys SET revoked_at = coalesce(revoked_at, now()), from_env = false
                   WHERE key_hash = %s""",
                (key_hash,),
            )

    def keys(self, project_id: UUID) -> list[KeyInfo]:
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT {KEY_COLUMNS} FROM api_keys k WHERE k.project_id = %s
                    ORDER BY k.revoked_at IS NOT NULL, k.created_at DESC""",
                (project_id,),
            ).fetchall()
        return [KeyInfo.model_validate(r) for r in rows]

    def project_key(self, project_id: UUID, key_id: UUID) -> KeyInfo | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {KEY_COLUMNS} FROM api_keys k WHERE k.project_id = %s AND k.id = %s",
                (project_id, key_id),
            ).fetchone()
        return KeyInfo.model_validate(row) if row else None

    def create_key(
        self,
        project_id: UUID,
        key_hash: str,
        name: str,
        scope: Scope,
        hint: str,
        created_by: UUID,
    ) -> KeyInfo:
        with self._connect() as conn:
            row = conn.execute(
                f"""INSERT INTO api_keys AS k
                      (key_hash, project_id, name, scope, hint, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s) RETURNING {KEY_COLUMNS}""",
                (key_hash, project_id, name, scope, hint, created_by),
            ).fetchone()
        return KeyInfo.model_validate(row)

    def revoke_key(self, project_id: UUID, key_id: UUID) -> KeyInfo:
        with self._connect() as conn:
            row = conn.execute(
                f"""UPDATE api_keys AS k SET revoked_at = coalesce(revoked_at, now())
                    WHERE project_id = %s AND id = %s RETURNING {KEY_COLUMNS}""",
                (project_id, key_id),
            ).fetchone()
        return KeyInfo.model_validate(row)

    # Projects

    def project(self, org_id: UUID, slug: str) -> Project | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {PROJECT_COLUMNS} FROM projects WHERE org_id = %s AND slug = %s",
                (org_id, slug),
            ).fetchone()
        return Project.model_validate(row) if row else None

    def project_by_id(self, project_id: UUID) -> Project | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {PROJECT_COLUMNS} FROM projects WHERE id = %s", (project_id,)
            ).fetchone()
        return Project.model_validate(row) if row else None

    def projects(self, org_id: UUID) -> list[Project]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {PROJECT_COLUMNS} FROM projects WHERE org_id = %s ORDER BY name",
                (org_id,),
            ).fetchall()
        return [Project.model_validate(r) for r in rows]

    def create_project(self, org_id: UUID, name: str, slug: str) -> Project | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    f"""INSERT INTO projects (org_id, name, slug) VALUES (%s, %s, %s)
                        RETURNING {PROJECT_COLUMNS}""",
                    (org_id, name, slug),
                ).fetchone()
        except psycopg.errors.UniqueViolation as e:
            if e.diag.constraint_name == "projects_slug_unique":
                return None
            raise Conflict(f'a project named "{name}" already exists') from e
        return Project.model_validate(row)

    def update_project(
        self, project_id: UUID, name: str | None, retention_days: int | None
    ) -> Project:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    f"""UPDATE projects SET name = coalesce(%s, name),
                          retention_days = coalesce(%s, retention_days)
                        WHERE id = %s RETURNING {PROJECT_COLUMNS}""",
                    (name, retention_days, project_id),
                ).fetchone()
        except psycopg.errors.UniqueViolation as e:
            raise Conflict(f'a project named "{name}" already exists') from e
        if row is None:
            raise NotFound("project not found")
        return Project.model_validate(row)

    # Claiming the install

    def has_users(self) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT EXISTS (SELECT 1 FROM users) AS e").fetchone()
        return bool(row and row["e"])

    def add_setup_token(self, token_hash: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO setup_tokens (token_hash) VALUES (%s) ON CONFLICT DO NOTHING",
                (token_hash,),
            )

    def setup_token_valid(self, token_hash: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM setup_tokens WHERE token_hash = %s", (token_hash,)
            ).fetchone()
        return row is not None

    def claim(self, org_id: UUID, email: str, password_hash: str) -> User | None:
        with self._connect() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (CLAIM_LOCK_ID,))
            if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                return None
            user = self._insert_user(conn, org_id, email, password_hash, "owner")
            conn.execute("DELETE FROM setup_tokens")
            return user

    # Users

    def login(self, email: str) -> tuple[User, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {USER_COLUMNS}, u.password_hash FROM users u WHERE u.email = %s",
                (email,),
            ).fetchone()
        return (User.model_validate(row), row["password_hash"]) if row else None

    def user(self, org_id: UUID, user_id: UUID) -> User | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {USER_COLUMNS} FROM users u WHERE u.org_id = %s AND u.id = %s",
                (org_id, user_id),
            ).fetchone()
        return User.model_validate(row) if row else None

    def users(self, org_id: UUID) -> list[User]:
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT {USER_COLUMNS} FROM users u WHERE u.org_id = %s
                    ORDER BY u.deactivated_at IS NOT NULL, u.created_at""",
                (org_id,),
            ).fetchall()
        return [User.model_validate(r) for r in rows]

    def set_password(self, user_id: UUID, password_hash: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user_id)
            )

    def update_member(
        self, org_id: UUID, user_id: UUID, role: Role | None, deactivated: bool | None
    ) -> User:
        with self._connect() as conn, conn.transaction():
            # Locking the org's active owners serializes two owners demoting each other.
            conn.execute(
                """SELECT id FROM users WHERE org_id = %s AND role = 'owner'
                   AND deactivated_at IS NULL FOR UPDATE""",
                (org_id,),
            )
            row = conn.execute(
                f"""UPDATE users u SET
                      role = coalesce(%s::text, role),
                      deactivated_at = CASE WHEN %s::boolean IS NULL THEN deactivated_at
                                            WHEN %s::boolean THEN coalesce(deactivated_at, now())
                                            ELSE NULL END
                    WHERE org_id = %s AND id = %s RETURNING {USER_COLUMNS}""",
                (role, deactivated, deactivated, org_id, user_id),
            ).fetchone()
            if row is None:
                raise NotFound("member not found")
            owners = conn.execute(
                """SELECT count(*) AS n FROM users WHERE org_id = %s AND role = 'owner'
                   AND deactivated_at IS NULL""",
                (org_id,),
            ).fetchone()
            if owners is None or owners["n"] == 0:
                raise Conflict("the org needs at least one active owner")
            if deactivated:
                conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
                # An owner who's removed can't come back through an invite they made earlier.
                conn.execute(
                    """UPDATE links SET used_at = now()
                       WHERE used_at IS NULL AND (created_by = %s OR user_id = %s)""",
                    (user_id, user_id),
                )
        return User.model_validate(row)

    def _insert_user(
        self,
        conn: psycopg.Connection[DictRow],
        org_id: UUID,
        email: str,
        password_hash: str,
        role: Role,
    ) -> User:
        try:
            with conn.transaction():  # a savepoint, so a taken email leaves the caller's tx usable
                row = conn.execute(
                    f"""INSERT INTO users AS u (org_id, email, password_hash, role)
                        VALUES (%s, %s, %s, %s) RETURNING {USER_COLUMNS}""",
                    (org_id, email, password_hash, role),
                ).fetchone()
        except psycopg.errors.UniqueViolation as e:
            raise Conflict(TAKEN) from e
        return User.model_validate(row)

    # Sessions

    def create_session(self, token_hash: str, user_id: UUID, expires_at: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
                (token_hash, user_id, expires_at),
            )

    def session(self, token_hash: str, now: datetime) -> tuple[User, datetime] | None:
        with self._connect() as conn:
            row = conn.execute(
                f"""SELECT {USER_COLUMNS}, s.expires_at FROM sessions s
                    JOIN users u ON u.id = s.user_id
                    WHERE s.token_hash = %s AND s.expires_at > %s
                      AND u.deactivated_at IS NULL""",
                (token_hash, now),
            ).fetchone()
        return (User.model_validate(row), row["expires_at"]) if row else None

    def extend_session(self, token_hash: str, expires_at: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET expires_at = %s WHERE token_hash = %s",
                (expires_at, token_hash),
            )

    def delete_session(self, token_hash: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash,))

    def delete_sessions(self, user_id: UUID, keep: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE user_id = %s AND token_hash IS DISTINCT FROM %s",
                (user_id, keep),
            )

    # Invite and reset links

    def create_link(
        self,
        token_hash: str,
        kind: LinkKind,
        org_id: UUID,
        role: Role | None,
        user_id: UUID | None,
        created_by: UUID,
        expires_at: datetime,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO links
                     (token_hash, kind, org_id, role, user_id, created_by, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (token_hash, kind, org_id, role, user_id, created_by, expires_at),
            )

    def link(self, token_hash: str, now: datetime) -> Link | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT kind, org_id, role, user_id, expires_at FROM links
                   WHERE token_hash = %s AND used_at IS NULL AND expires_at > %s""",
                (token_hash, now),
            ).fetchone()
        return Link.model_validate(row) if row else None

    def _take_link(
        self, conn: psycopg.Connection[DictRow], token_hash: str, now: datetime, kind: LinkKind
    ) -> DictRow | None:
        return conn.execute(
            """UPDATE links SET used_at = %s
               WHERE token_hash = %s AND kind = %s AND used_at IS NULL AND expires_at > %s
               RETURNING org_id, role, user_id""",
            (now, token_hash, kind, now),
        ).fetchone()

    def accept_invite(
        self, token_hash: str, now: datetime, email: str, password_hash: str
    ) -> User | None:
        with self._connect() as conn, conn.transaction():
            link = self._take_link(conn, token_hash, now, "invite")
            if link is None:
                return None
            # A taken email raises out of the transaction, which rolls back, so the invite
            # stays unused.
            return self._insert_user(conn, link["org_id"], email, password_hash, link["role"])

    def use_reset(self, token_hash: str, now: datetime, password_hash: str) -> UUID | None:
        with self._connect() as conn, conn.transaction():
            link = self._take_link(conn, token_hash, now, "reset")
            if link is None:
                return None
            conn.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (password_hash, link["user_id"]),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = %s", (link["user_id"],))
            return link["user_id"]

    def purge_expired(self, now: datetime) -> tuple[int, int]:
        with self._connect() as conn:
            sessions = conn.execute("DELETE FROM sessions WHERE expires_at <= %s", (now,))
            links = conn.execute("DELETE FROM links WHERE expires_at <= %s", (now,))
            return sessions.rowcount, links.rowcount


class RedisLimiter:
    def __init__(self, s: Settings):
        self.r = redis.Redis.from_url(s.redis_url, socket_timeout=2)

    # One hash per email, with each client's count (`n:<client>`) and hold (`until:<client>`), so
    # clearing an email after a sign-in or a reset is one DEL. Each failure pushes its window out.
    def _key(self, email: str) -> str:
        return f"login-failures:{email}"

    def _client_key(self, client: str) -> str:
        return f"login-client-failures:{client}"

    @contextmanager
    def _reaching(self) -> Iterator[None]:
        try:
            yield
        except redis.RedisError as e:
            raise Unavailable(f"can't reach Redis, which counts failed sign-ins: {e}") from e

    def held_until(self, email: str, client: str) -> float:
        with self._reaching():
            return float(self.r.hget(self._key(email), f"until:{client}") or 0)  # type: ignore[arg-type]

    def client_failures(self, client: str) -> int:
        with self._reaching():
            return int(self.r.get(self._client_key(client)) or 0)  # type: ignore[arg-type]

    def failed(self, email: str, client: str) -> int:
        with self._reaching():
            pipe = self.r.pipeline()
            pipe.hincrby(self._key(email), f"n:{client}", 1)
            pipe.expire(self._key(email), LOGIN_FAILURE_WINDOW_S)
            # Per client, the window starts at the first failure and isn't pushed out by later
            # ones, so a client turned away can try again 15 minutes after it started.
            pipe.incr(self._client_key(client))
            pipe.expire(self._client_key(client), LOGIN_FAILURE_WINDOW_S, nx=True)
            return int(pipe.execute()[0])

    def hold(self, email: str, client: str, until: float) -> None:
        with self._reaching():
            self.r.hset(self._key(email), f"until:{client}", until)

    def succeeded(self, email: str, client: str) -> None:
        with self._reaching():
            self.r.hdel(self._key(email), f"n:{client}", f"until:{client}")

    def clear(self, email: str) -> None:
        with self._reaching():
            self.r.delete(self._key(email))
