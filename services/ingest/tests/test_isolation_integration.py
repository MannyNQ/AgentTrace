"""Projects can't see into each other, against the compose stack (design.md, Phase 6 plan step 5).

Project A gets a trace, a published dataset, an experiment, a replay, and a key. Project B, in
the same org, then calls every project route at A's ids, once with B's full key and once with an
owner's session naming B: each read and write is 403 or 404, lists show none of A, and A is
unchanged. A test also checks the table below names every guarded route, so a new one has to be
added here.

The org and its projects stay behind, like the rows other integration tests leave; its user and
session are deleted, so the install stays as claimed or unclaimed as it was."""

import json
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from stack import emit_root, register_key

from agenttrace_ingest.app import create_app
from agenttrace_ingest.auth import PUBLIC_ROUTES, hash_token
from agenttrace_ingest.auth_store import PostgresAuth
from agenttrace_ingest.settings import Settings

pytestmark = pytest.mark.integration

# Routes that never take another project's ids: they act on the org, the caller, or the caller's
# own project. /v1/locate is org-wide by design, since members see every project.
NOT_PROJECT_SCOPED = {
    ("POST", "/v1/spans"),
    ("POST", "/v1/auth/logout"),
    ("GET", "/v1/auth/me"),
    ("POST", "/v1/auth/password"),
    ("GET", "/v1/members"),
    ("PATCH", "/v1/members/{user_id}"),
    ("POST", "/v1/members/invites"),
    ("POST", "/v1/members/{user_id}/reset"),
    ("POST", "/v1/keys"),
    ("GET", "/v1/projects"),
    ("POST", "/v1/projects"),
    ("PATCH", "/v1/project"),
    ("GET", "/v1/locate/{kind}/{entity_id}"),
}


@dataclass
class Project:
    id: uuid.UUID
    slug: str
    key: str
    trace: str = ""
    dataset: str = ""
    item: str = ""
    experiment: str = ""
    replay: str = ""
    key_id: str = ""

    def ids(self) -> list[str]:
        return [self.trace, self.dataset, self.item, self.experiment, self.replay, self.key_id]


@dataclass
class World:
    c: TestClient
    a: Project
    b: Project
    session: str


def as_key(p: Project) -> dict[str, str]:
    return {"Authorization": f"Bearer {p.key}"}


def experiment(p: Project, id: str) -> dict:
    return {
        "id": id,
        "dataset_id": p.dataset,
        "dataset_version": 1,
        "samples": 1,
        "scorers": [{"name": "exact", "type": "exact_match"}],
        "config": {"entry": "demo_agent:eval_entry"},
        "config_hash": "0" * 64,
    }


def replay(p: Project, id: str, root: str) -> dict:
    return {
        "id": id,
        "replay_of": {"trace_id": p.trace, "span_id": root},
        "runs": 1,
        "scorers": [],
        "config": {"entry": "demo_agent:eval_entry"},
        "config_hash": "0" * 64,
    }


def outcome(index_key: str, index: int, **extra) -> dict:
    """One sample or replay run, valid enough to reach the project check."""
    return {
        index_key: index,
        "started_at": "2026-10-01T00:00:00Z",
        "latency_ms": 1.0,
        "output": "A",
        "trajectory": [],
        "usage": [],
        "scores": [],
        **extra,
    }


def fill(c: TestClient, p: Project) -> None:
    """A trace, a dataset made from it and published, an experiment, and a replay."""
    h = as_key(p)
    p.trace = emit_root({"question": f"secret of {p.slug}"}, project_id=p.id)
    root = c.get(f"/v1/traces/{p.trace}/input", headers=h).json()["span_id"]
    name = f"it-{p.slug}"
    r = c.post("/v1/datasets", json={"name": name, "trace_id": p.trace}, headers=h)
    assert r.status_code == 201, r.text
    p.dataset = r.json()["id"]
    [item] = c.get(f"/v1/datasets/{p.dataset}/items", headers=h).json()["items"]
    p.item = item["item_id"]
    edit = {"rev": item["rev"], "contents": {**item["contents"], "expected_output": "A"}}
    assert c.patch(f"/v1/datasets/{p.dataset}/items/{p.item}", json=edit, headers=h).is_success
    assert c.post(f"/v1/datasets/{p.dataset}/publish", headers=h).status_code == 200
    p.experiment = str(uuid.uuid4())
    assert c.post("/v1/experiments", json=experiment(p, p.experiment), headers=h).status_code == 201
    p.replay = str(uuid.uuid4())
    r = c.post("/v1/replays", json=replay(p, p.replay, root), headers=h)
    assert r.status_code == 201, r.text


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    s = Settings()
    org = uuid.uuid4()
    tag = org.hex[:8]
    a = Project(uuid.uuid4(), f"a-{tag}", f"test-a-{uuid.uuid4()}")
    b = Project(uuid.uuid4(), f"b-{tag}", f"test-b-{uuid.uuid4()}")
    with TestClient(create_app(s)) as c:  # lifespan applies the migrations
        with psycopg.connect(s.postgres_dsn) as conn:
            conn.execute("INSERT INTO orgs (id, name) VALUES (%s, %s)", (org, f"it-{org}"))
            for p in (a, b):
                conn.execute(
                    "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, %s, %s)",
                    (p.id, org, p.slug, p.slug),
                )
            conn.execute(
                "INSERT INTO users (org_id, email, password_hash, role)"
                " VALUES (%s, %s, 'x', 'owner')",
                (org, f"{tag}@example.com"),
            )
        for p in (a, b):
            register_key(s, p.key, p.id)
        found = PostgresAuth(s).login(f"{tag}@example.com")
        assert found
        session = uuid.uuid4().hex
        PostgresAuth(s).create_session(
            hash_token(session), found[0].id, datetime.now(UTC) + timedelta(hours=1)
        )
        for p in (a, b):
            fill(c, p)
        a.key_id = c.get("/v1/keys", headers=session_on(session, a)).json()["keys"][0]["id"]
        try:
            yield World(c, a, b, session)
        finally:
            with psycopg.connect(s.postgres_dsn) as conn:
                users = "SELECT id FROM users WHERE org_id = %(org)s"
                conn.execute(f"DELETE FROM sessions WHERE user_id IN ({users})", {"org": org})
                conn.execute("DELETE FROM users WHERE org_id = %(org)s", {"org": org})


def session_on(session: str, p: Project) -> dict[str, str]:
    return {"Authorization": f"Session {session}", "AgentTrace-Project": p.slug}


ANALYTICS = ("volume", "latency", "cost", "tools", "models")


def at_a(w: World) -> list[tuple[str, str, dict | None]]:
    """Every project route, aimed at A's ids. Bodies are valid, so a refusal comes from the
    project check rather than validation."""
    a, b = w.a, w.b
    ds, item, exp, rp, t = a.dataset, a.item, a.experiment, a.replay, a.trace
    span = w.c.get(f"/v1/traces/{t}/input", headers=as_key(a)).json()["span_id"]
    b_root = w.c.get(f"/v1/traces/{b.trace}/input", headers=as_key(b)).json()["span_id"]
    return [
        ("GET", "/v1/keys", None),
        ("POST", f"/v1/keys/{a.key_id}/revoke", None),
        ("GET", "/v1/project", None),
        ("GET", "/v1/datasets", None),
        ("POST", "/v1/datasets", {"name": "stolen", "trace_id": t}),
        ("GET", f"/v1/datasets/{ds}", None),
        ("PATCH", f"/v1/datasets/{ds}", {"name": "renamed"}),
        ("GET", f"/v1/datasets/{ds}/items", None),
        ("POST", f"/v1/datasets/{ds}/items", {"input": "q"}),
        ("PATCH", f"/v1/datasets/{ds}/items/{item}", {"rev": 1, "contents": {"input": "x"}}),
        ("DELETE", f"/v1/datasets/{ds}/items/{item}?rev=1", None),
        ("POST", f"/v1/datasets/{ds}/items/from-trace", {"trace_id": t}),
        ("POST", f"/v1/datasets/{b.dataset}/items/from-trace", {"trace_id": t}),
        ("POST", f"/v1/datasets/{ds}/publish", None),
        ("GET", f"/v1/datasets/{ds}/versions", None),
        ("GET", f"/v1/datasets/{ds}/versions/1", None),
        ("GET", "/v1/experiments", None),
        ("GET", f"/v1/experiments?dataset_id={ds}", None),
        ("POST", "/v1/experiments", experiment(a, exp)),  # A's dataset
        ("POST", "/v1/experiments", experiment(b, exp)),  # B's dataset, A's experiment id
        ("GET", f"/v1/experiments/{exp}", None),
        (
            "POST",
            f"/v1/experiments/{exp}/samples",
            {"samples": [outcome("sample_index", 0, item_id=item)]},
        ),
        ("POST", f"/v1/experiments/{exp}/finish", {"status": "completed"}),
        ("GET", f"/v1/experiments/{exp}/comparison", None),
        ("GET", f"/v1/experiments/{exp}/items/{item}", None),
        ("GET", f"/v1/replays?trace_id={t}", None),
        ("POST", "/v1/replays", replay(a, str(uuid.uuid4()), span)),  # A's trace
        ("POST", "/v1/replays", replay(b, rp, b_root)),  # B's trace, A's replay id
        ("GET", f"/v1/replays/{rp}", None),
        (
            "POST",
            f"/v1/replays/{rp}/runs",
            {"runs": [outcome("run_index", 0, trace_id=uuid.uuid4().hex)]},
        ),
        ("POST", f"/v1/replays/{rp}/finish", {"status": "completed"}),
        ("GET", "/v1/traces", None),
        ("POST", "/v1/traces/live", {"trace_ids": [t]}),
        ("GET", f"/v1/traces/{t}", None),
        ("GET", f"/v1/traces/{t}/outcome", None),
        ("GET", f"/v1/traces/{t}/spans/{span}/input", None),
        ("GET", f"/v1/traces/{t}/input", None),
        *(("GET", f"/v1/analytics/{part}", None) for part in ANALYTICS),
    ]


def template(path: str, templates: list[str]) -> str:
    """The OpenAPI path a concrete path was made from, preferring literal segments, so
    `items/from-trace` isn't read as `items/{item_id}`."""
    bare = path.split("?")[0]
    for t in sorted(templates, key=lambda t: t.count("{")):
        if re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", t), bare):
            return t
    raise AssertionError(f"no route matches {path}")


def test_every_guarded_route_is_aimed_at_the_other_project(world: World):
    paths = world.c.app.openapi()["paths"]  # type: ignore[attr-defined]
    routes = {(m.upper(), p) for p, ops in paths.items() if p.startswith("/v1") for m in ops}
    covered = {(m, template(p, list(paths))) for m, p, _ in at_a(world)}
    assert covered == routes - PUBLIC_ROUTES - NOT_PROJECT_SCOPED


@pytest.mark.parametrize("who", ["key", "session"])
def test_the_other_project_can_t_read_or_change_anything(world: World, who: str):
    a, b = world.a, world.b
    h = as_key(b) if who == "key" else session_on(world.session, b)
    before = {
        path: world.c.get(path, headers=as_key(a)).json()
        for path in (
            f"/v1/datasets/{a.dataset}",
            f"/v1/datasets/{a.dataset}/items",
            f"/v1/experiments/{a.experiment}",
            f"/v1/replays/{a.replay}",
        )
    }
    secrets = [*a.ids(), a.slug, f"secret of {a.slug}"]
    for method, path, body in at_a(world):
        r = world.c.request(method, path, json=body, headers=h)
        what = f"{who} {method} {path} -> {r.status_code} {r.text[:300]}"
        assert r.status_code in (200, 403, 404, 409), what
        assert not any(s in r.text for s in secrets), what
        if r.status_code == 200:
            assert method == "GET" or path == "/v1/traces/live", what
    for path, was in before.items():
        assert world.c.get(path, headers=as_key(a)).json() == was, path
    keys = world.c.get("/v1/keys", headers=session_on(world.session, a)).json()["keys"]
    assert [k["id"] for k in keys if k["revoked_at"] is None] == [a.key_id]


def test_each_project_still_sees_its_own(world: World):
    a = world.a
    for h in (as_key(a), session_on(world.session, a)):
        listed = world.c.get("/v1/traces?limit=200", headers=h).json()["traces"]
        assert a.trace in [t["trace_id"] for t in listed]
        assert world.c.get(f"/v1/datasets/{a.dataset}", headers=h).status_code == 200
        volume = world.c.get("/v1/analytics/volume", headers=h).json()
        assert volume["totals"]["traces"] >= 1, volume
        assert json.loads(world.c.get("/v1/project", headers=h).text)["slug"] == a.slug
