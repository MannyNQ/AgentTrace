"""`PostgresAuth` against the compose stack: the 0005 migration, and the decisions its store makes
atomically (ADR-0032, ADR-0033, ADR-0034). Each test seeds its own org and deletes it after, so
it neither depends on nor changes whether the install is claimed."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

from agenttrace_ingest.app import create_app
from agenttrace_ingest.auth import DEFAULT_PROJECT_ID, User, hash_token
from agenttrace_ingest.auth_store import PostgresAuth, RedisLimiter
from agenttrace_ingest.datasets import Conflict
from agenttrace_ingest.settings import Settings

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)
LATER = NOW + timedelta(days=7)


@pytest.fixture(scope="module")
def store() -> PostgresAuth:
    s = Settings()
    with TestClient(create_app(s)):  # lifespan applies the migrations
        pass
    return PostgresAuth(s)


@pytest.fixture
def org() -> Iterator[tuple[uuid.UUID, uuid.UUID]]:
    """A fresh org with one project, yielding (org_id, project_id). It's deleted afterwards:
    an install counts as claimed once any user exists, so a leftover user would claim it."""
    org_id, project_id = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(Settings().postgres_dsn) as conn:
        conn.execute("INSERT INTO orgs (id, name) VALUES (%s, %s)", (org_id, f"it-{org_id}"))
        conn.execute(
            "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, 'p', 'p')",
            (project_id, org_id),
        )
    yield org_id, project_id
    with psycopg.connect(Settings().postgres_dsn) as conn:
        users = "SELECT id FROM users WHERE org_id = %(org)s"
        conn.execute(f"DELETE FROM sessions WHERE user_id IN ({users})", {"org": org_id})
        conn.execute("DELETE FROM links WHERE org_id = %(org)s", {"org": org_id})
        conn.execute("DELETE FROM api_keys WHERE project_id = %(p)s", {"p": project_id})
        conn.execute("DELETE FROM users WHERE org_id = %(org)s", {"org": org_id})
        conn.execute("DELETE FROM projects WHERE org_id = %(org)s", {"org": org_id})
        conn.execute("DELETE FROM orgs WHERE id = %(org)s", {"org": org_id})


def seed_user(org_id: uuid.UUID, role: str = "owner") -> User:
    email = f"{uuid.uuid4().hex[:12]}@example.com"
    with psycopg.connect(Settings().postgres_dsn) as conn:
        conn.execute(
            "INSERT INTO users (org_id, email, password_hash, role) VALUES (%s, %s, 'x', %s)",
            (org_id, email, role),
        )
    found = PostgresAuth(Settings()).login(email)
    assert found
    return found[0]


def test_the_default_project_is_slugged_default(store: PostgresAuth):
    with psycopg.connect(Settings().postgres_dsn) as conn:
        row = conn.execute("SELECT slug FROM projects WHERE id = %s", (DEFAULT_PROJECT_ID,))
        assert row.fetchone() == ("default",)


def test_keys_resolve_with_their_project_and_revoke_softly(store, org):
    org_id, project_id = org
    owner = seed_user(org_id)
    info = store.create_key(project_id, hash_token("k1"), "ci", "ingest", "k1", owner.id)
    row = store.key(hash_token("k1"))
    assert row and row.project.slug == "p" and row.info.scope == "ingest"
    store.touch_key(info.id)
    assert store.keys(project_id)[0].last_used_at is not None
    revoked = store.revoke_key(project_id, info.id)
    again = store.revoke_key(project_id, info.id)
    assert revoked.revoked_at is not None and again.revoked_at == revoked.revoked_at
    assert store.project_key(uuid.uuid4(), info.id) is None


def test_claiming_a_claimed_install_returns_none(store, org):
    seed_user(org[0])  # users exist now, whatever else the database holds
    assert store.claim(org[0], "late@example.com", "x") is None


def test_the_last_active_owner_stays(store, org):
    org_id, _ = org
    owner = seed_user(org_id)
    with pytest.raises(Conflict):
        store.update_member(org_id, owner.id, "member", None)
    with pytest.raises(Conflict):
        store.update_member(org_id, owner.id, None, True)
    assert store.user(org_id, owner.id) == owner  # rolled back
    second = seed_user(org_id)
    assert store.update_member(org_id, owner.id, "member", None).role == "member"
    with pytest.raises(Conflict):
        store.update_member(org_id, second.id, None, True)


def test_deactivating_deletes_sessions_and_hides_the_user(store, org):
    org_id, _ = org
    seed_user(org_id)
    member = seed_user(org_id, "member")
    store.create_session(hash_token("s-member"), member.id, LATER)
    assert store.session(hash_token("s-member"), NOW)
    assert store.update_member(org_id, member.id, None, True).deactivated
    assert store.session(hash_token("s-member"), NOW) is None
    assert store.update_member(org_id, member.id, None, False).deactivated is False


def test_sessions_expire_and_extend(store, org):
    user = seed_user(org[0])
    h = hash_token(f"s-{uuid.uuid4()}")
    store.create_session(h, user.id, NOW + timedelta(hours=1))
    assert store.session(h, NOW + timedelta(hours=2)) is None
    store.extend_session(h, NOW + timedelta(days=30))
    found = store.session(h, NOW + timedelta(hours=2))
    assert found and found[0].id == user.id
    other = hash_token(f"s-{uuid.uuid4()}")
    store.create_session(other, user.id, LATER)
    store.delete_sessions(user.id, keep=h)
    assert store.session(h, NOW) and store.session(other, NOW) is None


def test_an_invite_for_a_taken_email_stays_unused(store, org):
    org_id, _ = org
    owner = seed_user(org_id)
    h = hash_token(f"invite-{uuid.uuid4()}")
    store.create_link(h, "invite", org_id, "member", None, owner.id, LATER)
    with pytest.raises(Conflict):
        store.accept_invite(h, NOW, owner.email, "x")
    assert store.link(h, NOW) is not None
    user = store.accept_invite(h, NOW, f"{uuid.uuid4().hex[:12]}@example.com", "x")
    assert user and user.role == "member" and user.org_id == org_id
    assert store.link(h, NOW) is None
    assert store.accept_invite(h, NOW, "again@example.com", "x") is None


def test_a_reset_sets_the_password_once_and_signs_out(store, org):
    org_id, _ = org
    owner = seed_user(org_id)
    store.create_session(hash_token(f"s-{owner.id}"), owner.id, LATER)
    h = hash_token(f"reset-{uuid.uuid4()}")
    store.create_link(h, "reset", org_id, None, owner.id, owner.id, LATER)
    assert store.accept_invite(h, NOW, "x@example.com", "x") is None  # not an invite
    assert store.use_reset(h, NOW, "new-hash") == owner.id
    found = store.login(owner.email)
    assert found and found[1] == "new-hash"
    assert store.session(hash_token(f"s-{owner.id}"), NOW) is None
    assert store.use_reset(h, NOW, "other-hash") is None


def test_an_expired_link_can_t_be_used(store, org):
    org_id, _ = org
    owner = seed_user(org_id)
    h = hash_token(f"invite-{uuid.uuid4()}")
    store.create_link(h, "invite", org_id, "member", None, owner.id, NOW - timedelta(seconds=1))
    assert store.link(h, NOW) is None
    assert store.accept_invite(h, NOW, "late@example.com", "x") is None


def test_deactivating_voids_links_made_by_or_aimed_at_the_user(store, org):
    org_id, _ = org
    owner, rogue = seed_user(org_id), seed_user(org_id)
    member = seed_user(org_id, "member")
    planted, aimed, kept = (hash_token(f"l-{uuid.uuid4()}") for _ in range(3))
    store.create_link(planted, "invite", org_id, "owner", None, rogue.id, LATER)
    store.create_link(aimed, "reset", org_id, None, rogue.id, owner.id, LATER)
    store.create_link(kept, "reset", org_id, None, member.id, owner.id, LATER)
    store.update_member(org_id, rogue.id, None, True)
    assert store.link(planted, NOW) is None and store.link(aimed, NOW) is None
    assert store.link(kept, NOW) is not None


def test_revoking_by_hash_also_clears_from_env(store, org):
    org_id, project_id = org
    owner = seed_user(org_id)
    h = hash_token(f"k-{uuid.uuid4()}")
    store.create_key(project_id, h, "k", "full", "abcd", owner.id)
    store.revoke_key_hash(h)
    row = store.key(h)
    assert row and row.info.revoked_at is not None and not row.info.from_env
    store.revoke_key_hash(hash_token("no-such-key"))  # a no-op, not an error


def test_projects_tell_a_taken_slug_from_a_taken_name(store, org):
    org_id, _ = org
    made = store.create_project(org_id, "Support Bot", "support-bot")
    assert made and made.slug == "support-bot" and made.retention_days == 30
    assert store.create_project(org_id, "Other name", "support-bot") is None
    with pytest.raises(Conflict):
        store.create_project(org_id, "Support Bot", "support-bot-2")
    changed = store.update_project(made.id, "Helpdesk", 90)
    assert (changed.name, changed.slug, changed.retention_days) == ("Helpdesk", "support-bot", 90)
    assert [p.slug for p in store.projects(org_id)] == ["support-bot", "p"]
    assert store.project_by_id(made.id) == changed


def test_failed_logins_count_in_redis_and_clear():
    limiter = RedisLimiter(Settings())
    email = f"{uuid.uuid4().hex[:12]}@example.com"
    for _ in range(3):
        limiter.failed(email)
    assert limiter.failures(email) == 3
    ttl = limiter.r.ttl(f"login-failures:{email}")
    assert 0 < ttl <= 15 * 60  # type: ignore[operator]
    limiter.clear(email)
    assert limiter.failures(email) == 0


def test_the_tests_leave_the_install_as_they_found_it(store):
    """Runs last in this module: the org fixture's cleanup left no test users behind."""
    with psycopg.connect(Settings().postgres_dsn) as conn:
        row = conn.execute("SELECT count(*) FROM users WHERE email LIKE '%%@example.com'")
        assert row.fetchone() == (0,)
