"""Who is calling and what they may do (ADR-0032, ADR-0033, ADR-0034).

A request carries either an API key (`Authorization: Bearer <key>`), which resolves its project,
or a session (`Authorization: Session <token>`), which resolves a user and names its project in
the `Trayce-Project` header. Every `/v1` route depends on one of the guards at the bottom of
this module, apart from the few in `PUBLIC_ROUTES`; `tests/test_route_auth.py` enforces that.
Storage is behind `Store`; the rules are here.
"""

import hashlib
import hmac
import ipaddress
import logging
import math
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Literal, NamedTuple, Protocol, TypeVar
from urllib.parse import urlsplit
from uuid import UUID

import anyio
import anyio.to_thread
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Request

from trayce_ingest.errors import ApiError, Conflict, NotFound, Refused
from trayce_ingest.records import Record

log = logging.getLogger(__name__)

DEFAULT_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")  # migrations/0000_base.sql
DEFAULT_PROJECT_ID = UUID("00000000-0000-0000-0000-000000000001")
PROJECT_HEADER = "Trayce-Project"

SESSION_LIFETIME = timedelta(days=30)
SESSION_EXTEND_EVERY = timedelta(days=1)  # sliding, but written at most once a day
LINK_LIFETIME = timedelta(days=7)
MIN_PASSWORD_CHARS = 12
MAX_PASSWORD_CHARS = 1024
# Failed logins (ADR-0041). Per email and client: this many are free, then each earns that pair
# a wait twice as long as the last, up to MAX_LOGIN_BACKOFF_S. Keyed by client too, so failures
# from one address never make the user wait at another. Per client, across emails, this many in
# the window turn it away: that's what stops credential stuffing and random-email floods.
FREE_LOGIN_FAILURES = 5
MAX_LOGIN_BACKOFF_S = 15 * 60
MAX_CLIENT_LOGIN_FAILURES = 50
LOGIN_FAILURE_WINDOW_S = 15 * 60
# The web app names the browser's address in this header. Ingest believes it only from a peer
# in `Settings.trusted_forwarders`; from anyone else, the peer's own address is the client.
CLIENT_HEADER = "Trayce-Client-IP"
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
KEY_CACHE_S = 60.0  # how long a resolved key is trusted, and how often `last_used_at` is written
# The key in .env.example, the README, and the demo agent: public, so it's only honoured while
# TRAYCE_PUBLIC_URL is local (ADR-0033).
DEV_KEY = "tr-local-dev-key"
# Each argon2id hash takes 64 MiB, so at most this many run at once. Requests that hash a password
# wait for one as coroutines, not threads, so a login flood can't exhaust memory or the threadpool
# that span ingest and the other routes share.
HASHING_THREADS = 4
# Past this many waiting, a request that hashes is refused at once, so a flood of logins for
# random emails can't queue every real sign-in behind it until the web app's fetch times out.
MAX_HASHING_WAITERS = 32

# Routes anyone may call: they're how a caller gets a session in the first place.
PUBLIC_ROUTES = {
    ("GET", "/v1/auth/setup"),
    ("POST", "/v1/auth/setup"),
    ("POST", "/v1/auth/login"),
    ("GET", "/v1/auth/links/{token}"),
    ("POST", "/v1/auth/links/{token}/accept"),
    ("POST", "/v1/auth/links/{token}/reset"),
}
LINK_GONE = "this link has been used or has expired; ask an owner for a new one"
NO_CREDENTIALS = "sign in, or send an API key as `Authorization: Bearer <key>`"

Scope = Literal["ingest", "full"]
Role = Literal["owner", "member"]
LinkKind = Literal["invite", "reset"]


# --- Errors ---------------------------------------------------------------------------------


class Unauthorized(ApiError):
    status = 401


class Forbidden(ApiError):
    status = 403


class TooManyAttempts(ApiError):
    status = 429


# --- Records --------------------------------------------------------------------------------


class User(Record):
    id: UUID
    org_id: UUID
    email: str
    role: Role
    created_at: datetime
    deactivated: bool


class Project(Record):
    id: UUID
    org_id: UUID
    slug: str  # never changes; the name can (architecture.md, Phase 6)
    name: str
    retention_days: int  # applies to spans ingested from now on (ADR-0016)
    created_at: datetime


class KeyInfo(Record):
    """What the API shows about a key. The key itself is shown once, when it's created."""

    id: UUID
    project_id: UUID
    name: str
    scope: Scope
    hint: str | None  # the key's last 4 characters; unknown for keys made before Phase 6
    from_env: bool  # TRAYCE_API_KEY, re-added on every startup, so it can't be revoked
    created_by: UUID | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class KeyRow(Record):
    """A key as resolved for a request: its info plus its project."""

    info: KeyInfo
    project: Project


class Link(Record):
    kind: LinkKind
    org_id: UUID
    role: Role | None  # an invite's role
    user_id: UUID | None  # a reset's user
    expires_at: datetime


class NewSession(Record):
    token: str  # shown once; the web app keeps it in an httpOnly cookie
    expires_at: datetime
    user: User


class NewKey(Record):
    key: str  # shown once
    info: KeyInfo


class NewLink(Record):
    token: str  # shown once
    kind: LinkKind
    expires_at: datetime


# --- Storage interface ----------------------------------------------------------------------


class Store(Protocol):
    """Reads and writes rows; makes no decisions, except the ones that must be atomic, which
    each method names."""

    # API keys
    def key(self, key_hash: str) -> KeyRow | None: ...

    def touch_key(self, key_id: UUID) -> None:
        """Sets `last_used_at` to now."""
        ...

    def register_env_key(self, key_hash: str, hint: str, project_id: UUID) -> None:
        """Upserts the `.env` key as a live `full` key and makes it the only `from_env` one."""
        ...

    def revoke_key_hash(self, key_hash: str) -> None:
        """Revokes the key with this hash, if it exists, and clears its `from_env`."""
        ...

    def keys(self, project_id: UUID) -> list[KeyInfo]: ...

    def project_key(self, project_id: UUID, key_id: UUID) -> KeyInfo | None: ...

    def create_key(
        self,
        project_id: UUID,
        key_hash: str,
        name: str,
        scope: Scope,
        hint: str,
        created_by: UUID,
    ) -> KeyInfo: ...

    def revoke_key(self, project_id: UUID, key_id: UUID) -> KeyInfo:
        """Sets `revoked_at` if unset."""
        ...

    # Projects
    def project(self, org_id: UUID, slug: str) -> Project | None: ...

    def project_by_id(self, project_id: UUID) -> Project | None: ...

    def projects(self, org_id: UUID) -> list[Project]: ...

    def create_project(self, org_id: UUID, name: str, slug: str) -> Project | None:
        """None when the slug is taken in the org. Raises Conflict when the name is."""
        ...

    def update_project(
        self, project_id: UUID, name: str | None, retention_days: int | None
    ) -> Project:
        """Changes the given fields. Raises Conflict when the name is taken in the org."""
        ...

    # Claiming the install
    def has_users(self) -> bool: ...

    def add_setup_token(self, token_hash: str) -> None: ...

    def setup_token_valid(self, token_hash: str) -> bool: ...

    def claim(self, org_id: UUID, email: str, password_hash: str) -> User | None:
        """Atomically: creates the first user as owner and deletes every setup token, or returns
        None when a user already exists."""
        ...

    # Users
    def login(self, email: str) -> tuple[User, str] | None:
        """The user with this email and their password hash."""
        ...

    def user(self, org_id: UUID, user_id: UUID) -> User | None: ...

    def users(self, org_id: UUID) -> list[User]: ...

    def set_password(self, user_id: UUID, password_hash: str) -> None: ...

    def update_member(
        self, org_id: UUID, user_id: UUID, role: Role | None, deactivated: bool | None
    ) -> User:
        """Changes the given fields. Atomically raises Conflict when the org would be left
        without an active owner, and when deactivating, deletes the user's sessions and marks
        used every unused link they created or that would reset their password."""
        ...

    # Sessions
    def create_session(self, token_hash: str, user_id: UUID, expires_at: datetime) -> None: ...

    def session(self, token_hash: str, now: datetime) -> tuple[User, datetime] | None:
        """The session's active user and expiry, if it exists, hasn't expired, and its user
        isn't deactivated."""
        ...

    def extend_session(self, token_hash: str, expires_at: datetime) -> None: ...

    def delete_session(self, token_hash: str) -> None: ...

    def delete_sessions(self, user_id: UUID, keep: str | None = None) -> None:
        """Deletes the user's sessions, except `keep` (a token hash)."""
        ...

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
    ) -> None: ...

    def link(self, token_hash: str, now: datetime) -> Link | None:
        """The link, if it's unused and unexpired."""
        ...

    def accept_invite(
        self, token_hash: str, now: datetime, email: str, password_hash: str
    ) -> User | None:
        """Atomically: marks the invite used and creates its user, or returns None when it's
        used, expired, or not an invite. Raises Conflict, leaving the invite unused, when the
        email is taken."""
        ...

    def use_reset(self, token_hash: str, now: datetime, password_hash: str) -> UUID | None:
        """Atomically: marks the reset used, sets its user's password, and deletes their
        sessions; returns the user, or None when it's used, expired, or not a reset."""
        ...

    def purge_expired(self, now: datetime) -> tuple[int, int]:
        """Deletes sessions and links that expired by `now`; returns how many of each."""
        ...


class Admission(NamedTuple):
    wait_s: float  # > 0: the email-and-client pair is held this much longer
    client_full: bool  # the client has used up MAX_CLIENT_LOGIN_FAILURES


class Limiter(Protocol):
    """Counts failed logins per email-and-client pair, each over `LOGIN_FAILURE_WINDOW_S` of that
    pair's quiet, and per client over a window from its first failure. Raises `Unavailable` when
    it can't count, and signing in is refused rather than left unlimited."""

    def admit(self, email: str, client: str, now: float) -> Admission:
        """In one atomic step: refuses while the client is full or the pair is held; otherwise
        counts this attempt as a failure for both up front, holds the pair for
        `login_backoff_s(count)` from `now`, and admits it. Counting before the password is
        checked is what keeps a burst of concurrent guesses from all slipping through."""
        ...

    def succeeded(self, email: str, client: str) -> None:
        """Undoes an admitted attempt that signed in: clears the pair, and gives the client back
        the one failure `admit` counted. Other clients' counts stay."""
        ...

    def clear(self, email: str) -> None:
        """Forgets the email's failures and holds, from every client."""
        ...


def login_backoff_s(failures: int) -> float:
    """How long an email-and-client pair waits after its `failures`-th failure."""
    if failures < FREE_LOGIN_FAILURES:
        return 0.0
    return float(min(2 ** (failures - FREE_LOGIN_FAILURES), MAX_LOGIN_BACKOFF_S))


def client_address(request: Request, trusted: list[IPNetwork]) -> str:
    """The caller's address: the web app's CLIENT_HEADER when the peer is trusted to send it,
    else the peer's."""
    peer = request.client.host if request.client else "unknown"
    peer_ip = _ip(peer)
    if peer_ip is None:
        return peer
    named = _ip(request.headers.get(CLIENT_HEADER, ""))
    if named is not None and any(peer_ip in net for net in trusted):
        return str(named)
    return str(peer_ip)


def _ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The address in `text`, with an IPv4-mapped IPv6 address (`::ffff:10.0.0.2`, from a
    dual-stack socket) as its IPv4 address, so it matches IPv4 ranges and counts as one client."""
    try:
        ip = ipaddress.ip_address(text.strip())
    except ValueError:
        return None
    return (ip.ipv4_mapped or ip) if isinstance(ip, ipaddress.IPv6Address) else ip


# --- Rules ----------------------------------------------------------------------------------


def hash_token(token: str) -> str:
    """Keys, sessions, links, and setup tokens are all stored as SHA-256 hex. They're random,
    so a slow hash adds nothing."""
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(32)


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    local, at, domain = email.partition("@")
    # A dotless domain is allowed: self-hosted teams use addresses like `ops@localhost`.
    if not at or not local or not domain or "@" in domain or len(email) > 254:
        raise Refused("that doesn't look like an email address")
    return email


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_CHARS:
        raise Refused(f"a password needs at least {MIN_PASSWORD_CHARS} characters")
    if len(password) > MAX_PASSWORD_CHARS:
        raise Refused(f"a password can have at most {MAX_PASSWORD_CHARS} characters")


def is_local(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")


T = TypeVar("T")


@dataclass(frozen=True)
class Caller:
    """A resolved request. `user` is set for sessions; API keys leave it None."""

    project_id: UUID
    user: User | None = None


@dataclass(frozen=True)
class Session:
    """A signed-in user and their session token."""

    user: User
    token: str


@dataclass(frozen=True)
class Owner:
    """A signed-in owner. Building one from anyone else raises Forbidden, and the owner-only
    rules take an Owner, so a route that forgets its owner guard can't reach them (#31)."""

    user: User

    def __post_init__(self) -> None:
        if self.user.role != "owner" or self.user.deactivated:
            raise Forbidden("only an owner can do this")


@dataclass(frozen=True)
class ProjectOwner:
    """An owner acting on one project: managing its keys or its settings."""

    owner: Owner
    project_id: UUID


@dataclass
class _Cached:
    row: KeyRow
    at: float


class Auth:
    def __init__(
        self,
        store: Store,
        limiter: Limiter,
        setup_token: str | None = None,
        clock=time.monotonic,
        now=lambda: datetime.now(UTC),
    ):
        self.store, self.limiter, self.setup_token = store, limiter, setup_token or None
        self.clock, self.now = clock, now
        self.passwords = PasswordHasher()  # argon2id
        self._dummy_hash = self.passwords.hash(secrets.token_urlsafe(16))
        self._keys: dict[str, _Cached] = {}
        self._hashing: anyio.CapacityLimiter | None = None  # made in the event loop, on first use
        self._hashing_queued = 0  # hashing or waiting to

    async def hashing(self, fn: Callable[..., T], *args) -> T:
        """Runs a rule that hashes a password on at most HASHING_THREADS threads, with at most
        MAX_HASHING_WAITERS waiting for one."""
        if self._hashing is None:
            self._hashing = anyio.CapacityLimiter(HASHING_THREADS)
        # Counted here, not from the limiter's statistics: a task only shows there as waiting
        # once it reaches a checkpoint, so a burst arriving together would all see none.
        if self._hashing_queued >= HASHING_THREADS + MAX_HASHING_WAITERS:
            raise TooManyAttempts("too many sign-ins at once; try again in a moment")
        self._hashing_queued += 1
        try:
            return await anyio.to_thread.run_sync(partial(fn, *args), limiter=self._hashing)
        finally:
            self._hashing_queued -= 1

    # --- Resolving a request ---

    def resolve_key(self, key: str) -> KeyRow | None:
        """The live key, from a cache trusted for `KEY_CACHE_S`. Each refetch also writes
        `last_used_at`, so busy span ingest writes it at most once a minute per key. A key
        revoked by another ingest process stops working there within that minute."""
        h = hash_token(key)
        cached = self._keys.get(h)
        if cached and self.clock() - cached.at < KEY_CACHE_S:
            return cached.row
        row = self.store.key(h)
        if row is None or row.info.revoked_at is not None:
            self._keys.pop(h, None)
            return None
        self.store.touch_key(row.info.id)
        self._keys[h] = _Cached(row, self.clock())
        return row

    def resolve_session(self, token: str) -> User | None:
        h = hash_token(token)
        now = self.now()
        found = self.store.session(h, now)
        if found is None:
            return None
        user, expires_at = found
        if expires_at - now < SESSION_LIFETIME - SESSION_EXTEND_EVERY:
            self.store.extend_session(h, now + SESSION_LIFETIME)
        return user

    def credentials(self, request: Request) -> tuple[str, str]:
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        scheme, value = scheme.lower(), value.strip()
        if scheme not in ("bearer", "session") or not value:
            raise Unauthorized(NO_CREDENTIALS)
        return scheme, value

    def caller(self, request: Request, scope: Scope) -> Caller:
        """The project a request acts on, checked against its key's scope or its session's
        org. Unknown projects and projects in another org are both 404, so a slug's existence
        isn't revealed."""
        scheme, value = self.credentials(request)
        slug = request.headers.get(PROJECT_HEADER, "").strip()
        if scheme == "bearer":
            return self._key_caller(self.resolve_key(value), slug, scope)
        user = self.resolve_session(value)
        if user is None:
            raise Unauthorized("your session has expired; sign in again")
        if not slug:
            raise Refused(f"name a project with the {PROJECT_HEADER} header")
        project = self.store.project(user.org_id, slug)
        if project is None:
            raise NotFound("project not found")
        return Caller(project_id=project.id, user=user)

    def cached_caller(self, request: Request, scope: Scope) -> Caller | None:
        """`caller` for an API key already in the cache, which needs no I/O, so the guard can
        answer on the event loop; None means `caller` has to do the lookup."""
        scheme, value = self.credentials(request)
        if scheme != "bearer":
            return None
        cached = self._keys.get(hash_token(value))
        if cached is None or self.clock() - cached.at >= KEY_CACHE_S:
            return None
        return self._key_caller(cached.row, request.headers.get(PROJECT_HEADER, "").strip(), scope)

    def _key_caller(self, row: KeyRow | None, slug: str, scope: Scope) -> Caller:
        if row is None:
            raise Unauthorized("unknown or revoked API key")
        if slug and slug != row.project.slug:
            raise Forbidden(f'this API key belongs to project "{row.project.slug}", not "{slug}"')
        if scope == "full" and row.info.scope != "full":
            raise Forbidden(f"this needs a full API key; this one's scope is {row.info.scope}")
        return Caller(project_id=row.project.id)

    def signed_in(self, request: Request) -> tuple[User, str]:
        """A session's user and token. API keys act for a project, not a person, so they're
        refused here."""
        scheme, value = self.credentials(request)
        if scheme != "session":
            raise Forbidden("this needs a signed-in user; API keys can't do it")
        user = self.resolve_session(value)
        if user is None:
            raise Unauthorized("your session has expired; sign in again")
        return user, value

    # --- Claiming the install (ADR-0034) ---

    def prepare_setup(self) -> str | None:
        """On startup, while the install is unclaimed and no `TRAYCE_SETUP_TOKEN` is set:
        a fresh token, stored for any ingest process to accept, for the caller to log."""
        if self.setup_token or self.store.has_users():
            return None
        token = new_token()
        self.store.add_setup_token(hash_token(token))
        return token

    def claimed(self) -> bool:
        return self.store.has_users()

    def claim(self, token: str, email: str, password: str) -> NewSession:
        if self.store.has_users():
            raise Conflict("this install already has an owner; sign in instead")
        if not self._setup_token_ok(token):
            raise Forbidden("wrong setup token; it's in ingest's logs or TRAYCE_SETUP_TOKEN")
        email = normalize_email(email)
        check_password(password)
        user = self.store.claim(DEFAULT_ORG_ID, email, self.passwords.hash(password))
        if user is None:
            raise Conflict("this install already has an owner; sign in instead")
        return self._new_session(user)

    def _setup_token_ok(self, token: str) -> bool:
        if self.setup_token:
            return hmac.compare_digest(token.encode(), self.setup_token.encode())
        return bool(token) and self.store.setup_token_valid(hash_token(token))

    # --- Signing in and out ---

    def login(self, email: str, password: str, client: str) -> NewSession:
        try:
            email = normalize_email(email)
        except Refused:
            raise Unauthorized("wrong email or password") from None
        admission = self.limiter.admit(email, client, self.now().timestamp())
        if admission.client_full:
            raise TooManyAttempts("too many failed sign-ins from your address; try again later")
        if admission.wait_s > 0:
            # Refused before hashing, even for the right password: a held pair is no oracle.
            s = math.ceil(admission.wait_s)
            raise TooManyAttempts(
                f"too many failed sign-ins; try again in {s} second{'' if s == 1 else 's'}"
            )
        found = self.store.login(email)
        # Hash something either way, so an unknown email takes as long as a wrong password.
        ok = self._verify(found[1] if found else self._dummy_hash, password)
        if found is None or not ok or found[0].deactivated:
            raise Unauthorized("wrong email or password")  # `admit` already counted it
        user, stored = found
        if self.passwords.check_needs_rehash(stored):
            self.store.set_password(user.id, self.passwords.hash(password))
        # Only this client's: the owner signing in mustn't hand an attacker fresh free tries.
        self.limiter.succeeded(email, client)
        return self._new_session(user)

    def logout(self, token: str) -> None:
        self.store.delete_session(hash_token(token))

    def change_password(self, user: User, token: str, current: str, new: str) -> None:
        found = self.store.login(user.email)
        if found is None or not self._verify(found[1], current):
            raise Forbidden("the current password is wrong")
        check_password(new)
        self.store.set_password(user.id, self.passwords.hash(new))
        self.store.delete_sessions(user.id, keep=hash_token(token))

    def set_password(self, email: str, password: str) -> User:
        """For `trayce-admin reset-password`: no link, no current password."""
        check_password(password)
        found = self.store.login(normalize_email(email))
        if found is None:
            raise NotFound(f"no user with the email {email}")
        user = found[0]
        self.limiter.clear(user.email)  # first: with Redis down, nothing changes
        self.store.set_password(user.id, self.passwords.hash(password))
        self.store.delete_sessions(user.id)
        return user

    def _verify(self, stored: str, password: str) -> bool:
        try:
            return self.passwords.verify(stored, password)
        except (VerificationError, InvalidHashError):
            return False

    def purge_expired(self) -> tuple[int, int]:
        """Expired sessions and links are refused when used; this deletes their rows."""
        return self.store.purge_expired(self.now())

    def _new_session(self, user: User) -> NewSession:
        token = new_token()
        expires_at = self.now() + SESSION_LIFETIME
        self.store.create_session(hash_token(token), user.id, expires_at)
        return NewSession(token=token, expires_at=expires_at, user=user)

    # --- Members, invites, and resets ---

    def members(self, owner: Owner) -> list[User]:
        return self.store.users(owner.user.org_id)

    def update_member(
        self, owner: Owner, user_id: UUID, role: Role | None, deactivated: bool | None
    ) -> User:
        org_id = owner.user.org_id
        if self.store.user(org_id, user_id) is None:
            raise NotFound("member not found")
        return self.store.update_member(org_id, user_id, role, deactivated)

    def invite(self, owner: Owner, role: Role) -> NewLink:
        return self._new_link(owner, "invite", role=role)

    def reset_link(self, owner: Owner, user_id: UUID) -> NewLink:
        user = self.store.user(owner.user.org_id, user_id)
        if user is None:
            raise NotFound("member not found")
        if user.deactivated:
            raise Refused("this member is deactivated; reactivate them first")
        return self._new_link(owner, "reset", user_id=user_id)

    def _new_link(
        self, owner: Owner, kind: LinkKind, role: Role | None = None, user_id: UUID | None = None
    ) -> NewLink:
        token = new_token()
        expires_at = self.now() + LINK_LIFETIME
        by = owner.user
        self.store.create_link(hash_token(token), kind, by.org_id, role, user_id, by.id, expires_at)
        return NewLink(token=token, kind=kind, expires_at=expires_at)

    def link(self, token: str) -> Link:
        link = self.store.link(hash_token(token), self.now())
        if link is None:
            raise NotFound(LINK_GONE)
        return link

    def accept_invite(self, token: str, email: str, password: str) -> NewSession:
        self._link(token, "invite")
        email = normalize_email(email)
        check_password(password)
        user = self.store.accept_invite(
            hash_token(token), self.now(), email, self.passwords.hash(password)
        )
        if user is None:
            raise NotFound(LINK_GONE)
        return self._new_session(user)

    def reset_password(self, token: str, password: str) -> None:
        self._link(token, "reset")
        check_password(password)
        if (
            self.store.use_reset(hash_token(token), self.now(), self.passwords.hash(password))
            is None
        ):
            raise NotFound(LINK_GONE)

    def _link(self, token: str, kind: LinkKind) -> Link:
        link = self.link(token)
        if link.kind != kind:
            raise NotFound(LINK_GONE)
        return link

    # --- API keys (ADR-0033) ---

    def register_env_key(
        self, key: str, public_url: str, project_id: UUID = DEFAULT_PROJECT_ID
    ) -> bool:
        """Registers the `.env` key, unless it's the published DEV_KEY on an install that isn't
        local: then this returns False for the caller to log."""
        if self.refuse_dev_key(public_url) and key == DEV_KEY:
            return False
        self.store.register_env_key(hash_token(key), key[-4:], project_id)
        return True

    def refuse_dev_key(self, public_url: str) -> bool:
        """On an install that isn't local, revokes DEV_KEY whichever key `.env` names now: an
        earlier, local start may have registered it, and a rotated `.env` key is otherwise left
        working (ADR-0033). Returns whether the install refuses it."""
        if is_local(public_url):
            return False
        self.store.revoke_key_hash(hash_token(DEV_KEY))
        self._keys.pop(hash_token(DEV_KEY), None)
        return True

    def keys(self, by: ProjectOwner) -> list[KeyInfo]:
        return self.store.keys(by.project_id)

    def create_key(self, by: ProjectOwner, name: str, scope: Scope) -> NewKey:
        name = name.strip()
        if not name:
            raise Refused("a key needs a name")
        key = new_token("tr_")
        info = self.store.create_key(
            by.project_id, hash_token(key), name, scope, key[-4:], by.owner.user.id
        )
        return NewKey(key=key, info=info)

    def revoke_key(self, by: ProjectOwner, key_id: UUID) -> KeyInfo:
        project_id = by.project_id
        info = self.store.project_key(project_id, key_id)
        if info is None:
            raise NotFound("key not found")
        if info.from_env:
            raise Conflict(
                "this key comes from TRAYCE_API_KEY and is re-added on every startup; "
                "change it in .env instead"
            )
        revoked = self.store.revoke_key(project_id, key_id)
        self._keys = {h: c for h, c in self._keys.items() if c.row.info.id != key_id}
        return revoked


# --- Guards ---------------------------------------------------------------------------------
# FastAPI dependencies.


class ProjectAccess:
    """Resolves the project a route acts on. `scope="ingest"` admits `ingest` keys too."""

    def __init__(self, auth: Auth, scope: Scope = "full"):
        self.auth = auth
        self.scope: Scope = scope

    async def __call__(self, request: Request) -> UUID:
        caller = self.auth.cached_caller(request, self.scope)
        if caller is None:
            caller = await anyio.to_thread.run_sync(self.auth.caller, request, self.scope)
        return caller.project_id


class OwnerProject:
    """A project route only an owner's session may call."""

    def __init__(self, auth: Auth):
        self.auth = auth

    def __call__(self, request: Request) -> ProjectOwner:
        caller = self.auth.caller(request, "full")
        if caller.user is None:
            raise Forbidden("this needs a signed-in owner; API keys can't do it")
        return ProjectOwner(Owner(caller.user), caller.project_id)


class SignedIn:
    """A route for any signed-in user."""

    def __init__(self, auth: Auth):
        self.auth = auth

    def __call__(self, request: Request) -> Session:
        return Session(*self.auth.signed_in(request))


class SignedInOwner:
    """A route only an owner's session may call, outside any one project."""

    def __init__(self, auth: Auth):
        self.auth = auth

    def __call__(self, request: Request) -> Owner:
        return Owner(self.auth.signed_in(request)[0])
