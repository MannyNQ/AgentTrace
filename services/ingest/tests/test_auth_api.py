"""Keys, sessions, claiming, invites, resets, members, and key management through the API, with
in-memory storage (ADR-0032, ADR-0033, ADR-0034)."""

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import anyio
import httpx
import pytest
from fake_auth import FakeAuth, FakeLimiter
from fake_datasets import FakeDatasets, FakePins
from fastapi.testclient import TestClient

from agenttrace import new_span_id, new_trace_id
from agenttrace_ingest.app import create_app, purge_expired_forever
from agenttrace_ingest.auth import (
    DEV_KEY,
    HASHING_THREADS,
    KEY_CACHE_S,
    MAX_LOGIN_FAILURES,
    SESSION_EXTEND_EVERY,
    SESSION_LIFETIME,
    Auth,
    hash_token,
    is_local,
)
from agenttrace_ingest.auth_store import RedisLimiter
from agenttrace_ingest.settings import Settings
from agenttrace_ingest.traces import Unavailable

PROJECT = UUID("00000000-0000-0000-0000-0000000000aa")  # slug p-aa
OTHER = UUID("00000000-0000-0000-0000-0000000000bb")  # slug p-bb, same org
FOREIGN = UUID("00000000-0000-0000-0000-0000000000cc")  # another org
FOREIGN_ORG = UUID("00000000-0000-0000-0000-00000000c0c0")
SETUP = "the-setup-token"
OWNER = ("owner@example.com", "owner-password-1")
PASSWORD = "member-password-1"
LOCAL = "http://localhost:3000"


class Queue:
    def __init__(self):
        self.pushed: list[UUID] = []

    async def push(self, project_id: UUID, spans_json: str) -> None:
        self.pushed.append(project_id)


class Api:
    def __init__(self, **settings):
        self.store = FakeAuth.with_keys({"full-key": PROJECT, "other-key": OTHER})
        self.store.add_key("ingest-key", PROJECT, "ingest")
        self.store.add_project(FOREIGN, "foreign", org_id=FOREIGN_ORG)
        self.limiter = FakeLimiter()
        self.queue = Queue()
        self.datasets = FakeDatasets()
        settings.setdefault("agenttrace_setup_token", SETUP)
        app = create_app(
            Settings(**settings),
            checks={},
            auth_store=self.store,
            limiter=self.limiter,
            datasets=self.datasets,
            pins=FakePins(),
            queue=self.queue,
        )
        self.c = TestClient(app)

    def call(
        self,
        method: str,
        path: str,
        json=None,
        key: str | None = None,
        session: str | None = None,
        project: str | None = None,
        status: int | None = None,
    ):
        headers = {}
        if key is not None:
            headers["Authorization"] = f"Bearer {key}"
        if session is not None:
            headers["Authorization"] = f"Session {session}"
        if project is not None:
            headers["AgentTrace-Project"] = project
        r = self.c.request(method, path, json=json, headers=headers)
        if status is not None:
            assert r.status_code == status, r.text
        return r

    def error(self, r) -> str:
        return r.json()["detail"]["error"]

    def claim(self) -> str:
        email, password = OWNER
        body = {"token": SETUP, "email": email, "password": password}
        return self.call("POST", "/v1/auth/setup", body, status=201).json()["token"]

    def invite(self, owner: str, email: str, role="member") -> str:
        link = self.call("POST", "/v1/members/invites", {"role": role}, session=owner, status=201)
        body = {"email": email, "password": PASSWORD}
        r = self.call("POST", f"/v1/auth/links/{link.json()['token']}/accept", body, status=201)
        return r.json()["token"]

    def login(self, email: str, password: str, status=200):
        return self.call(
            "POST", "/v1/auth/login", {"email": email, "password": password}, status=status
        )

    def user_id(self, session: str) -> str:
        return self.call("GET", "/v1/auth/me", session=session, status=200).json()["user"]["id"]

    def spans(self, **kw):
        span = {
            "trace_id": new_trace_id(),
            "span_id": new_span_id(),
            "name": "run",
            "kind": "agent",
            "start_time": "2026-10-25T12:00:00Z",
        }
        return self.call("POST", "/v1/spans", {"spans": [span]}, **kw)


@pytest.fixture
def api() -> Api:
    return Api()


# --- API keys -------------------------------------------------------------------------------


def test_a_full_key_acts_on_its_project(api: Api):
    api.call("POST", "/v1/datasets", {"name": "refunds"}, key="full-key", status=201)
    names = api.call("GET", "/v1/datasets", key="full-key", status=200).json()["datasets"]
    assert [d["name"] for d in names] == ["refunds"]
    assert api.call("GET", "/v1/datasets", key="other-key").json()["datasets"] == []


def test_an_ingest_key_can_only_send_spans(api: Api):
    api.spans(key="ingest-key", status=202)
    assert api.queue.pushed == [PROJECT]
    r = api.call("GET", "/v1/datasets", key="ingest-key", status=403)
    assert "needs a full API key" in api.error(r) and "ingest" in api.error(r)
    api.call("GET", "/v1/experiments", key="ingest-key", status=403)


def test_a_key_naming_another_project_is_403(api: Api):
    api.call("GET", "/v1/datasets", key="full-key", project="p-aa", status=200)
    r = api.call("GET", "/v1/datasets", key="full-key", project="p-bb", status=403)
    assert '"p-aa"' in api.error(r)


def test_missing_unknown_and_malformed_credentials_are_401(api: Api):
    api.call("GET", "/v1/datasets", status=401)
    api.call("GET", "/v1/datasets", key="nope", status=401)
    r = api.c.get("/v1/datasets", headers={"Authorization": "Basic full-key"})
    assert r.status_code == 401
    assert api.queue.pushed == []


def test_a_revoked_key_stops_working_in_the_process_that_revoked_it(api: Api):
    owner = api.claim()
    made = api.call(
        "POST", "/v1/keys", {"name": "ci", "scope": "full"}, session=owner, project="p-aa"
    ).json()
    api.call("GET", "/v1/datasets", key=made["key"], status=200)  # now cached
    api.call(
        "POST", f"/v1/keys/{made['info']['id']}/revoke", session=owner, project="p-aa", status=200
    )
    api.call("GET", "/v1/datasets", key=made["key"], status=401)


def test_keys_are_trusted_for_a_minute_and_touched_once_per_refetch():
    store = FakeAuth.with_keys({"k": PROJECT})
    t = [0.0]
    a = Auth(store, FakeLimiter(), clock=lambda: t[0])
    for _ in range(5):
        assert a.resolve_key("k") is not None
    assert len(store.touched) == 1  # last_used_at written once, not per batch
    store.revoke_key(PROJECT, store.keys(PROJECT)[0].id)  # revoked by another ingest process
    assert a.resolve_key("k") is not None  # still cached here
    t[0] = KEY_CACHE_S + 1
    assert a.resolve_key("k") is None


def test_the_env_key_is_full_and_can_t_be_revoked(api: Api):
    owner = api.claim()
    Auth(api.store, api.limiter).register_env_key("env-key-1234", LOCAL, PROJECT)
    keys = api.call("GET", "/v1/keys", session=owner, project="p-aa", status=200).json()["keys"]
    env = next(k for k in keys if k["from_env"])
    assert env["scope"] == "full" and env["hint"] == "1234" and env["name"] == ".env"
    r = api.call("POST", f"/v1/keys/{env['id']}/revoke", session=owner, project="p-aa", status=409)
    assert "AGENTTRACE_API_KEY" in api.error(r)
    api.call("GET", "/v1/datasets", key="env-key-1234", status=200)


def test_a_new_env_key_releases_the_old_one(api: Api):
    a = Auth(api.store, api.limiter)
    a.register_env_key("old-env-key", LOCAL, PROJECT)
    a.register_env_key("new-env-key", LOCAL, PROJECT)
    by_hash = {h: k.from_env for h, k in api.store.api_keys.items()}
    assert by_hash[hash_token("new-env-key")] is True
    assert by_hash[hash_token("old-env-key")] is False  # now an ordinary, revocable key


@pytest.mark.parametrize(
    "url, local",
    [
        ("http://localhost:3000", True),
        ("http://127.0.0.1:13000", True),
        ("http://[::1]:3000", True),
        ("http://agenttrace.localhost", True),
        ("https://agenttrace.example.com", False),
        ("https://localhost.example.com", False),
        ("http://10.0.0.5:3000", False),
    ],
)
def test_only_localhost_urls_are_local(url: str, local: bool):
    assert is_local(url) is local


def test_the_dev_key_works_on_a_local_install(api: Api):
    assert Auth(api.store, api.limiter).register_env_key(DEV_KEY, LOCAL, PROJECT)
    api.call("GET", "/v1/datasets", key=DEV_KEY, status=200)


def test_the_dev_key_is_refused_and_revoked_once_the_install_isn_t_local():
    api = Api()
    a = Auth(api.store, api.limiter)
    a.register_env_key(DEV_KEY, LOCAL, PROJECT)  # an earlier start, before going public
    assert not a.register_env_key(DEV_KEY, "https://agenttrace.example.com", PROJECT)
    info = api.store.api_keys[hash_token(DEV_KEY)]
    assert info.revoked_at is not None and not info.from_env
    api.call("GET", "/v1/datasets", key=DEV_KEY, status=401)


def test_a_key_of_your_own_works_on_a_public_install(api: Api):
    a = Auth(api.store, api.limiter)
    assert a.register_env_key("at_my-own-secret", "https://agenttrace.example.com", PROJECT)
    api.call("GET", "/v1/datasets", key="at_my-own-secret", status=200)


# --- Managing keys --------------------------------------------------------------------------


def test_an_owner_creates_a_key_shown_once(api: Api):
    owner = api.claim()
    r = api.call(
        "POST",
        "/v1/keys",
        {"name": "prod agent", "scope": "ingest"},
        session=owner,
        project="p-aa",
        status=201,
    ).json()
    key, info = r["key"], r["info"]
    assert key.startswith("at_") and info["hint"] == key[-4:] and info["scope"] == "ingest"
    listed = api.call("GET", "/v1/keys", session=owner, project="p-aa").text
    assert key not in listed and "prod agent" in listed
    api.spans(key=key, status=202)
    api.call("GET", "/v1/datasets", key=key, status=403)


def test_only_an_owner_session_manages_keys(api: Api):
    owner = api.claim()
    member = api.invite(owner, "m@example.com")
    api.call("GET", "/v1/keys", session=member, project="p-aa", status=403)
    r = api.call("GET", "/v1/keys", key="full-key", status=403)
    assert "API keys can't" in api.error(r)
    body = {"name": "x", "scope": "full"}
    api.call("POST", "/v1/keys", body, session=member, project="p-aa", status=403)


def test_a_key_from_another_project_can_t_be_revoked_by_id(api: Api):
    owner = api.claim()
    other = api.call(
        "POST", "/v1/keys", {"name": "b", "scope": "full"}, session=owner, project="p-bb"
    )
    key_id = other.json()["info"]["id"]
    api.call("POST", f"/v1/keys/{key_id}/revoke", session=owner, project="p-aa", status=404)


# --- Sessions -------------------------------------------------------------------------------


def test_a_session_names_its_project_in_a_header(api: Api):
    owner = api.claim()
    api.call("POST", "/v1/datasets", {"name": "a"}, session=owner, project="p-aa", status=201)
    api.call("POST", "/v1/datasets", {"name": "b"}, session=owner, project="p-bb", status=201)
    listed = api.call("GET", "/v1/datasets", session=owner, project="p-aa").json()["datasets"]
    assert [d["name"] for d in listed] == ["a"]
    r = api.call("GET", "/v1/datasets", session=owner, status=422)
    assert "AgentTrace-Project" in api.error(r)


def test_another_orgs_project_looks_like_no_project(api: Api):
    owner = api.claim()
    foreign = api.call("GET", "/v1/datasets", session=owner, project="foreign", status=404)
    missing = api.call("GET", "/v1/datasets", session=owner, project="nope", status=404)
    assert foreign.json() == missing.json()


def test_an_unknown_or_logged_out_session_is_401(api: Api):
    owner = api.claim()
    api.call("GET", "/v1/datasets", session="made-up", project="p-aa", status=401)
    api.call("POST", "/v1/auth/logout", session=owner, status=204)
    api.call("GET", "/v1/datasets", session=owner, project="p-aa", status=401)


def test_sessions_slide_but_are_written_at_most_once_a_day():
    store = FakeAuth()
    now = [datetime(2026, 10, 1, tzinfo=UTC)]
    a = Auth(store, FakeLimiter(), setup_token=SETUP, now=lambda: now[0])
    session = a.claim(SETUP, *OWNER)
    h = hash_token(session.token)
    now[0] += SESSION_EXTEND_EVERY / 2
    a.resolve_session(session.token)
    assert store.sessions[h][1] == session.expires_at  # not yet a day: unchanged
    now[0] += SESSION_EXTEND_EVERY
    a.resolve_session(session.token)
    assert store.sessions[h][1] == now[0] + SESSION_LIFETIME
    now[0] += SESSION_LIFETIME + timedelta(seconds=1)
    assert a.resolve_session(session.token) is None


def test_api_keys_can_t_use_session_routes(api: Api):
    r = api.call("GET", "/v1/auth/me", key="full-key", status=403)
    assert "signed-in user" in api.error(r)
    api.call("GET", "/v1/members", key="full-key", status=403)


# --- Claiming the install -------------------------------------------------------------------


def test_claiming_with_the_setup_token_makes_the_owner(api: Api):
    assert api.call("GET", "/v1/auth/setup").json() == {"claimed": False}
    body = {"token": "wrong", "email": OWNER[0], "password": OWNER[1]}
    api.call("POST", "/v1/auth/setup", body, status=403)
    owner = api.claim()
    me = api.call("GET", "/v1/auth/me", session=owner, status=200).json()["user"]
    assert me["role"] == "owner" and me["email"] == OWNER[0]
    assert api.call("GET", "/v1/auth/setup").json() == {"claimed": True}
    body = {"token": SETUP, "email": "late@example.com", "password": OWNER[1]}
    api.call("POST", "/v1/auth/setup", body, status=409)


def test_without_the_env_token_a_printed_token_claims_it():
    api = Api(agenttrace_setup_token=None)
    printed = Auth(api.store, api.limiter).prepare_setup()  # what another ingest process logged
    assert printed
    body = {"token": printed, "email": OWNER[0], "password": OWNER[1]}
    api.call("POST", "/v1/auth/setup", {**body, "token": ""}, status=403)
    api.call("POST", "/v1/auth/setup", body, status=201)
    assert api.store.setup_tokens == set()
    assert Auth(api.store, api.limiter).prepare_setup() is None  # claimed: nothing to print


def test_setup_checks_email_and_password(api: Api):
    short = {"token": SETUP, "email": OWNER[0], "password": "short"}
    assert "12 characters" in api.error(api.call("POST", "/v1/auth/setup", short, status=422))
    bad = {"token": SETUP, "email": "not-an-email", "password": OWNER[1]}
    api.call("POST", "/v1/auth/setup", bad, status=422)
    assert api.call("GET", "/v1/auth/setup").json() == {"claimed": False}


# --- Signing in -----------------------------------------------------------------------------


def test_login_takes_any_email_case(api: Api):
    api.claim()
    session = api.login(OWNER[0].upper(), OWNER[1]).json()
    assert session["user"]["email"] == OWNER[0]


def test_wrong_password_and_unknown_email_look_the_same(api: Api):
    api.claim()
    wrong = api.login(OWNER[0], "not-the-password", status=401).json()
    unknown = api.login("who@example.com", "not-the-password", status=401).json()
    assert wrong == unknown


def test_ten_failures_lock_the_email_even_for_the_right_password(api: Api):
    api.claim()
    for _ in range(MAX_LOGIN_FAILURES):
        api.login(OWNER[0], "not-the-password", status=401)
    api.login(OWNER[0], OWNER[1], status=429)


def test_a_successful_login_clears_the_failures(api: Api):
    api.claim()
    for _ in range(MAX_LOGIN_FAILURES - 1):
        api.login(OWNER[0], "not-the-password", status=401)
    api.login(OWNER[0], OWNER[1], status=200)
    assert api.limiter.failures(OWNER[0]) == 0


def test_changing_the_password_signs_out_other_sessions(api: Api):
    here = api.claim()
    elsewhere = api.login(*OWNER).json()["token"]
    body = {"current": "wrong-password!", "new": "brand-new-password"}
    api.call("POST", "/v1/auth/password", body, session=here, status=403)
    body = {"current": OWNER[1], "new": "brand-new-password"}
    api.call("POST", "/v1/auth/password", body, session=here, status=204)
    api.call("GET", "/v1/auth/me", session=here, status=200)
    api.call("GET", "/v1/auth/me", session=elsewhere, status=401)
    api.login(OWNER[0], "brand-new-password", status=200)


def test_admin_set_password_signs_the_user_out_and_unlocks_them(api: Api):
    owner = api.claim()
    for _ in range(MAX_LOGIN_FAILURES):
        api.login(OWNER[0], "not-the-password", status=401)
    Auth(api.store, api.limiter).set_password(OWNER[0], "recovered-password")
    api.call("GET", "/v1/auth/me", session=owner, status=401)
    api.login(OWNER[0], "recovered-password", status=200)


# --- Invites, resets, and members -----------------------------------------------------------


def test_an_invite_makes_a_member_once(api: Api):
    owner = api.claim()
    link = api.call("POST", "/v1/members/invites", {}, session=owner, status=201).json()
    shown = api.call("GET", f"/v1/auth/links/{link['token']}", status=200).json()
    assert shown["kind"] == "invite" and shown["role"] == "member"
    body = {"email": "m@example.com", "password": PASSWORD}
    member = api.call("POST", f"/v1/auth/links/{link['token']}/accept", body, status=201).json()
    assert member["user"]["role"] == "member"
    api.call("GET", "/v1/datasets", session=member["token"], project="p-bb", status=200)
    again = {"email": "n@example.com", "password": PASSWORD}
    api.call("POST", f"/v1/auth/links/{link['token']}/accept", again, status=404)
    api.call("GET", f"/v1/auth/links/{link['token']}", status=404)


def test_an_invite_for_a_taken_email_stays_usable(api: Api):
    owner = api.claim()
    link = api.call("POST", "/v1/members/invites", {}, session=owner).json()["token"]
    taken = {"email": OWNER[0], "password": PASSWORD}
    api.call("POST", f"/v1/auth/links/{link}/accept", taken, status=409)
    fresh = {"email": "m@example.com", "password": PASSWORD}
    api.call("POST", f"/v1/auth/links/{link}/accept", fresh, status=201)


def test_an_expired_link_is_gone(api: Api):
    owner = api.claim()
    token = api.call("POST", "/v1/members/invites", {}, session=owner).json()["token"]
    link = api.store.links[hash_token(token)]
    link.link = link.link.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(1)})
    api.call("GET", f"/v1/auth/links/{token}", status=404)


def test_a_reset_link_sets_the_password_and_signs_out(api: Api):
    owner = api.claim()
    member = api.invite(owner, "m@example.com")
    member_id = api.user_id(member)
    reset = api.call("POST", f"/v1/members/{member_id}/reset", session=owner, status=201)
    token = reset.json()["token"]
    # A reset link isn't an invite, and the other way round.
    invite_body = {"email": "x@example.com", "password": PASSWORD}
    api.call("POST", f"/v1/auth/links/{token}/accept", invite_body, status=404)
    api.call("POST", f"/v1/auth/links/{token}/reset", {"password": "reset-password-1"}, status=204)
    api.call("GET", "/v1/auth/me", session=member, status=401)
    api.login("m@example.com", "reset-password-1", status=200)
    api.call("POST", f"/v1/auth/links/{token}/reset", {"password": "again-password-1"}, status=404)


def test_members_are_managed_by_owners_only(api: Api):
    owner = api.claim()
    member = api.invite(owner, "m@example.com")
    api.call("GET", "/v1/members", session=member, status=403)
    api.call("POST", "/v1/members/invites", {}, session=member, status=403)
    listed = api.call("GET", "/v1/members", session=owner, status=200).json()["members"]
    assert [m["email"] for m in listed] == [OWNER[0], "m@example.com"]


def test_deactivating_a_member_signs_them_out_and_keeps_their_keys(api: Api):
    owner = api.claim()
    second = api.invite(owner, "o2@example.com", role="owner")
    made = api.call(
        "POST", "/v1/keys", {"name": "ci", "scope": "full"}, session=second, project="p-aa"
    )
    second_id = api.user_id(second)
    body = {"deactivated": True}
    r = api.call("PATCH", f"/v1/members/{second_id}", body, session=owner, status=200)
    assert r.json()["deactivated"] is True
    api.call("GET", "/v1/auth/me", session=second, status=401)
    api.login("o2@example.com", PASSWORD, status=401)
    api.call("GET", "/v1/datasets", key=made.json()["key"], status=200)


def test_deactivating_an_owner_voids_the_links_they_made(api: Api):
    owner = api.claim()
    rogue = api.invite(owner, "rogue@example.com", role="owner")
    member = api.invite(owner, "m@example.com")
    planted = api.call("POST", "/v1/members/invites", {"role": "owner"}, session=rogue).json()
    reset = api.call("POST", f"/v1/members/{api.user_id(member)}/reset", session=rogue).json()
    mine = api.call("POST", "/v1/members/invites", {}, session=owner).json()
    rogue_id = api.user_id(rogue)
    api.call("PATCH", f"/v1/members/{rogue_id}", {"deactivated": True}, session=owner, status=200)
    back_in = {"email": "rogue2@example.com", "password": PASSWORD}
    api.call("POST", f"/v1/auth/links/{planted['token']}/accept", back_in, status=404)
    takeover = {"password": "taken-over-pass"}
    api.call("POST", f"/v1/auth/links/{reset['token']}/reset", takeover, status=404)
    api.call("GET", f"/v1/auth/links/{mine['token']}", status=200)  # other owners' links stand


def test_deactivating_a_member_voids_resets_aimed_at_them(api: Api):
    owner = api.claim()
    member = api.invite(owner, "m@example.com")
    member_id = api.user_id(member)
    reset = api.call("POST", f"/v1/members/{member_id}/reset", session=owner).json()
    api.call("PATCH", f"/v1/members/{member_id}", {"deactivated": True}, session=owner)
    api.call("GET", f"/v1/auth/links/{reset['token']}", status=404)


def test_the_last_owner_can_t_be_demoted_or_deactivated(api: Api):
    owner = api.claim()
    me = api.user_id(owner)
    api.call("PATCH", f"/v1/members/{me}", {"role": "member"}, session=owner, status=409)
    api.call("PATCH", f"/v1/members/{me}", {"deactivated": True}, session=owner, status=409)
    second = api.invite(owner, "o2@example.com", role="owner")
    api.call("PATCH", f"/v1/members/{me}", {"role": "member"}, session=owner, status=200)
    # Now a member, the first user can't manage anyone; the second owner still can.
    api.call("GET", "/v1/members", session=owner, status=403)
    api.call("GET", "/v1/members", session=second, status=200)


def test_a_member_of_another_org_is_not_found(api: Api):
    owner = api.claim()
    api.call(
        "PATCH",
        "/v1/members/00000000-0000-0000-0000-000000000099",
        {"role": "owner"},
        session=owner,
        status=404,
    )


def test_error_bodies_use_the_api_shape(api: Api):
    r = api.call("GET", "/v1/datasets", key="nope", status=401)
    assert json.loads(r.text) == {"detail": {"error": "unknown or revoked API key"}}


# --- Password hashing is bounded ------------------------------------------------------------


def test_logins_hash_a_few_at_a_time_while_spans_keep_flowing(monkeypatch):
    """A burst of logins queues for HASHING_THREADS hashing threads; a span batch with a cached
    key is answered meanwhile, since its guard never waits for a thread."""
    lock, running, peak = threading.Lock(), [0], [0]

    def slow_verify(self, stored: str, password: str) -> bool:
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.05)
        with lock:
            running[0] -= 1
        return False

    monkeypatch.setattr(Auth, "_verify", slow_verify)
    app = Api().c.app
    span_ms: list[float] = []
    key = {"Authorization": "Bearer full-key"}

    async def main():
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await c.post("/v1/spans", json={"spans": []}, headers=key)  # caches the key

            async def login(i: int):
                body = {"email": f"u{i}@example.com", "password": "not-the-password"}
                assert (await c.post("/v1/auth/login", json=body)).status_code == 401

            async def span_batch():
                await anyio.sleep(0.02)  # once the logins are queued
                t = time.perf_counter()
                assert (
                    await c.post("/v1/spans", json={"spans": []}, headers=key)
                ).status_code == 202
                span_ms.append((time.perf_counter() - t) * 1000)

            async with anyio.create_task_group() as tg:
                for i in range(HASHING_THREADS * 4):
                    tg.start_soon(login, i)
                tg.start_soon(span_batch)

    anyio.run(main)
    assert peak[0] == HASHING_THREADS
    assert span_ms[0] < 100  # not stuck behind 16 x 50 ms of hashing


# --- Housekeeping and outages ---------------------------------------------------------------


def test_a_dotless_email_domain_is_an_email():
    api = Api()
    body = {"token": SETUP, "email": "Ops@LocalHost", "password": OWNER[1]}
    api.call("POST", "/v1/auth/setup", body, status=201)
    api.login("ops@localhost", OWNER[1], status=200)
    for bad in ("ops", "@localhost", "ops@", "ops@local@host"):
        api.login(bad, OWNER[1], status=401)


def test_purging_deletes_only_expired_sessions_and_links(api: Api):
    owner = api.claim()
    stale = api.invite(owner, "stale@example.com")
    api.store.sessions[hash_token(stale)] = (UUID(api.user_id(stale)), datetime.now(UTC))
    live_link = api.call("POST", "/v1/members/invites", {}, session=owner).json()["token"]
    old_link = api.call("POST", "/v1/members/invites", {}, session=owner).json()["token"]
    link = api.store.links[hash_token(old_link)]
    link.link = link.link.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(1)})

    assert Auth(api.store, api.limiter).purge_expired() == (1, 1)
    assert set(api.store.sessions) == {hash_token(owner)}
    assert set(api.store.links) >= {hash_token(live_link)}
    assert hash_token(old_link) not in api.store.links
    api.call("GET", "/v1/auth/me", session=owner, status=200)


class DownLimiter:
    """Redis is unreachable: every count fails, as `RedisLimiter` reports it."""

    def __init__(self):
        self.calls = 0

    def _down(self, *_):
        self.calls += 1
        raise Unavailable("can't reach Redis, which counts failed sign-ins: refused")

    failures = failed = clear = _down


def test_signing_in_while_redis_is_down_is_503_not_500(api: Api):
    api.claim()
    down = DownLimiter()
    api.limiter.failures = api.limiter.failed = api.limiter.clear = down._down  # type: ignore[method-assign]
    r = api.login(*OWNER, status=503)
    assert "can't reach Redis" in api.error(r)


def test_an_admin_reset_while_redis_is_down_changes_nothing(api: Api):
    owner = api.claim()
    with pytest.raises(Unavailable):
        Auth(api.store, DownLimiter()).set_password(OWNER[0], "recovered-password")
    api.call("GET", "/v1/auth/me", session=owner, status=200)
    api.login(*OWNER, status=200)


def test_the_redis_limiter_reports_an_unreachable_redis():
    limiter = RedisLimiter(Settings(redis_url="redis://127.0.0.1:1/0"))
    for call in (limiter.failures, limiter.failed, limiter.clear):
        with pytest.raises(Unavailable, match="can't reach Redis"):
            call("ops@example.com")


def test_the_purge_loop_survives_a_failed_pass():
    calls: list[int] = []

    class Flaky:
        def purge_expired(self) -> tuple[int, int]:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("postgres is down")
            return (0, 0)

    async def run() -> None:
        with anyio.move_on_after(0.5):
            await purge_expired_forever(Flaky(), every_s=0.01)  # type: ignore[arg-type]

    anyio.run(run)
    assert len(calls) >= 2
