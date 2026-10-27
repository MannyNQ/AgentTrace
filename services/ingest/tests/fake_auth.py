"""In-memory auth storage with the same contract as `auth_store.PostgresAuth`, including the
atomic decisions it names. The rules under test are in `agenttrace_ingest.auth`."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from agenttrace_ingest.auth import (
    DEFAULT_ORG_ID,
    KeyInfo,
    KeyRow,
    Link,
    LinkKind,
    Project,
    Role,
    Scope,
    User,
    hash_token,
)
from agenttrace_ingest.datasets import Conflict, NotFound


@dataclass
class _User:
    user: User
    password_hash: str


@dataclass
class _Link:
    link: Link
    created_by: UUID
    used: bool = False


@dataclass
class FakeAuth:
    projects_by_id: dict[UUID, Project] = field(default_factory=dict)
    api_keys: dict[str, KeyInfo] = field(default_factory=dict)  # by hash
    users_by_id: dict[UUID, _User] = field(default_factory=dict)
    sessions: dict[str, tuple[UUID, datetime]] = field(default_factory=dict)
    links: dict[str, _Link] = field(default_factory=dict)
    setup_tokens: set[str] = field(default_factory=set)
    touched: list[UUID] = field(default_factory=list)

    @classmethod
    def with_keys(cls, keys: dict[str, UUID], scope: Scope = "full") -> "FakeAuth":
        """Each key a live key for its project; each project gets the slug `p-<last 2 hex>`."""
        store = cls()
        for key, project in keys.items():
            store.add_project(project, f"p-{project.hex[-2:]}")
            store.add_key(key, project, scope)
        return store

    def add_project(self, project_id: UUID, slug: str, org_id: UUID = DEFAULT_ORG_ID) -> None:
        self.projects_by_id[project_id] = Project(
            id=project_id,
            org_id=org_id,
            slug=slug,
            name=slug,
            retention_days=30,
            created_at=datetime.now(UTC),
        )

    def add_key(self, key: str, project_id: UUID, scope: Scope = "full") -> KeyInfo:
        info = KeyInfo(
            id=uuid4(),
            project_id=project_id,
            name=key,
            scope=scope,
            hint=key[-4:],
            from_env=False,
            created_by=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            revoked_at=None,
        )
        self.api_keys[hash_token(key)] = info
        return info

    # API keys

    def key(self, key_hash: str) -> KeyRow | None:
        info = self.api_keys.get(key_hash)
        return KeyRow(info=info, project=self.projects_by_id[info.project_id]) if info else None

    def touch_key(self, key_id: UUID) -> None:
        self.touched.append(key_id)

    def register_env_key(self, key_hash: str, hint: str, project_id: UUID) -> None:
        for h, info in self.api_keys.items():
            if info.from_env and h != key_hash:
                self.api_keys[h] = info.model_copy(update={"from_env": False})
        old = self.api_keys.get(key_hash)
        self.api_keys[key_hash] = KeyInfo(
            id=old.id if old else uuid4(),
            project_id=project_id,
            name=old.name if old and old.name else ".env",
            scope="full",
            hint=hint,
            from_env=True,
            created_by=old.created_by if old else None,
            created_at=old.created_at if old else datetime.now(UTC),
            last_used_at=old.last_used_at if old else None,
            revoked_at=None,
        )

    def revoke_key_hash(self, key_hash: str) -> None:
        info = self.api_keys.get(key_hash)
        if info:
            revoked_at = info.revoked_at or datetime.now(UTC)
            self.api_keys[key_hash] = info.model_copy(
                update={"revoked_at": revoked_at, "from_env": False}
            )

    def keys(self, project_id: UUID) -> list[KeyInfo]:
        found = [k for k in self.api_keys.values() if k.project_id == project_id]
        return sorted(found, key=lambda k: (k.revoked_at is not None, -k.created_at.timestamp()))

    def project_key(self, project_id: UUID, key_id: UUID) -> KeyInfo | None:
        return next((k for k in self.keys(project_id) if k.id == key_id), None)

    def create_key(
        self,
        project_id: UUID,
        key_hash: str,
        name: str,
        scope: Scope,
        hint: str,
        created_by: UUID,
    ) -> KeyInfo:
        info = KeyInfo(
            id=uuid4(),
            project_id=project_id,
            name=name,
            scope=scope,
            hint=hint,
            from_env=False,
            created_by=created_by,
            created_at=datetime.now(UTC),
            last_used_at=None,
            revoked_at=None,
        )
        self.api_keys[key_hash] = info
        return info

    def revoke_key(self, project_id: UUID, key_id: UUID) -> KeyInfo:
        for h, info in self.api_keys.items():
            if info.project_id == project_id and info.id == key_id:
                revoked = info.model_copy(
                    update={"revoked_at": info.revoked_at or datetime.now(UTC)}
                )
                self.api_keys[h] = revoked
                return revoked
        raise AssertionError("revoke_key on a missing key")

    # Projects

    def project(self, org_id: UUID, slug: str) -> Project | None:
        return next(
            (p for p in self.projects_by_id.values() if p.org_id == org_id and p.slug == slug), None
        )

    def project_by_id(self, project_id: UUID) -> Project | None:
        return self.projects_by_id.get(project_id)

    def projects(self, org_id: UUID) -> list[Project]:
        return sorted(
            (p for p in self.projects_by_id.values() if p.org_id == org_id), key=lambda p: p.name
        )

    def create_project(self, org_id: UUID, name: str, slug: str) -> Project | None:
        mine = self.projects(org_id)
        if any(p.slug == slug for p in mine):
            return None
        if any(p.name == name for p in mine):
            raise Conflict(f'a project named "{name}" already exists')
        project_id = uuid4()
        self.add_project(project_id, slug, org_id)
        self.projects_by_id[project_id] = self.projects_by_id[project_id].model_copy(
            update={"name": name}
        )
        return self.projects_by_id[project_id]

    def update_project(
        self, project_id: UUID, name: str | None, retention_days: int | None
    ) -> Project:
        p = self.projects_by_id[project_id]
        if name is not None and any(
            o.name == name and o.id != project_id for o in self.projects(p.org_id)
        ):
            raise Conflict(f'a project named "{name}" already exists')
        update = {
            k: v for k, v in (("name", name), ("retention_days", retention_days)) if v is not None
        }
        self.projects_by_id[project_id] = p.model_copy(update=update)
        return self.projects_by_id[project_id]

    # Claiming the install

    def has_users(self) -> bool:
        return bool(self.users_by_id)

    def add_setup_token(self, token_hash: str) -> None:
        self.setup_tokens.add(token_hash)

    def setup_token_valid(self, token_hash: str) -> bool:
        return token_hash in self.setup_tokens

    def claim(self, org_id: UUID, email: str, password_hash: str) -> User | None:
        if self.users_by_id:
            return None
        self.setup_tokens.clear()
        return self._insert_user(org_id, email, password_hash, "owner")

    # Users

    def _insert_user(self, org_id: UUID, email: str, password_hash: str, role: Role) -> User:
        if any(u.user.email == email for u in self.users_by_id.values()):
            raise Conflict("a user with this email already exists")
        user = User(
            id=uuid4(),
            org_id=org_id,
            email=email,
            role=role,
            created_at=datetime.now(UTC),
            deactivated=False,
        )
        self.users_by_id[user.id] = _User(user, password_hash)
        return user

    def login(self, email: str) -> tuple[User, str] | None:
        found = next((u for u in self.users_by_id.values() if u.user.email == email), None)
        return (found.user, found.password_hash) if found else None

    def user(self, org_id: UUID, user_id: UUID) -> User | None:
        found = self.users_by_id.get(user_id)
        return found.user if found and found.user.org_id == org_id else None

    def users(self, org_id: UUID) -> list[User]:
        found = [u.user for u in self.users_by_id.values() if u.user.org_id == org_id]
        return sorted(found, key=lambda u: (u.deactivated, u.created_at))

    def set_password(self, user_id: UUID, password_hash: str) -> None:
        self.users_by_id[user_id].password_hash = password_hash

    def update_member(
        self, org_id: UUID, user_id: UUID, role: Role | None, deactivated: bool | None
    ) -> User:
        found = self.users_by_id.get(user_id)
        if found is None or found.user.org_id != org_id:
            raise NotFound("member not found")
        update: dict = {}
        if role is not None:
            update["role"] = role
        if deactivated is not None:
            update["deactivated"] = deactivated
        changed = found.user.model_copy(update=update)
        others = [u.user for u in self.users_by_id.values() if u.user.id != user_id]
        owners = [u for u in [*others, changed] if u.org_id == org_id and u.role == "owner"]
        if not any(not u.deactivated for u in owners):
            raise Conflict("the org needs at least one active owner")
        found.user = changed
        if deactivated:
            self.delete_sessions(user_id)
            for link in self.links.values():
                if link.created_by == user_id or link.link.user_id == user_id:
                    link.used = True
        return changed

    # Sessions

    def create_session(self, token_hash: str, user_id: UUID, expires_at: datetime) -> None:
        self.sessions[token_hash] = (user_id, expires_at)

    def session(self, token_hash: str, now: datetime) -> tuple[User, datetime] | None:
        found = self.sessions.get(token_hash)
        if found is None or found[1] <= now:
            return None
        user = self.users_by_id[found[0]].user
        return None if user.deactivated else (user, found[1])

    def extend_session(self, token_hash: str, expires_at: datetime) -> None:
        user_id, _ = self.sessions[token_hash]
        self.sessions[token_hash] = (user_id, expires_at)

    def delete_session(self, token_hash: str) -> None:
        self.sessions.pop(token_hash, None)

    def delete_sessions(self, user_id: UUID, keep: str | None = None) -> None:
        self.sessions = {h: s for h, s in self.sessions.items() if s[0] != user_id or h == keep}

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
        link = Link(kind=kind, org_id=org_id, role=role, user_id=user_id, expires_at=expires_at)
        self.links[token_hash] = _Link(link, created_by)

    def link(self, token_hash: str, now: datetime) -> Link | None:
        found = self.links.get(token_hash)
        if found is None or found.used or found.link.expires_at <= now:
            return None
        return found.link

    def accept_invite(
        self, token_hash: str, now: datetime, email: str, password_hash: str
    ) -> User | None:
        link = self.link(token_hash, now)
        if link is None or link.kind != "invite" or link.role is None:
            return None
        user = self._insert_user(link.org_id, email, password_hash, link.role)  # may raise
        self.links[token_hash].used = True
        return user

    def use_reset(self, token_hash: str, now: datetime, password_hash: str) -> UUID | None:
        link = self.link(token_hash, now)
        if link is None or link.kind != "reset" or link.user_id is None:
            return None
        self.links[token_hash].used = True
        self.set_password(link.user_id, password_hash)
        self.delete_sessions(link.user_id)
        return link.user_id

    def purge_expired(self, now: datetime) -> tuple[int, int]:
        sessions = {h: s for h, s in self.sessions.items() if s[1] > now}
        links = {h: lk for h, lk in self.links.items() if lk.link.expires_at > now}
        purged = (len(self.sessions) - len(sessions), len(self.links) - len(links))
        self.sessions, self.links = sessions, links
        return purged


@dataclass
class FakeLimiter:
    counts: dict[str, int] = field(default_factory=dict)

    def failures(self, email: str) -> int:
        return self.counts.get(email, 0)

    def failed(self, email: str) -> None:
        self.counts[email] = self.counts.get(email, 0) + 1

    def clear(self, email: str) -> None:
        self.counts.pop(email, None)
