"""The resolver's whole matrix in one table: each kind of credential against each kind of route
(architecture.md, Phase 6 plan step 5). The other auth tests explain the cases one at a time; this
one keeps every combination pinned, so a change to one guard can't quietly open another."""

from datetime import UTC, datetime, timedelta

import pytest
from test_auth_api import OTHER, PROJECT, Api

from agenttrace_ingest.auth import hash_token

# The project slugs FakeAuth gives PROJECT and OTHER; "foreign" is another org's project.
OWN, SIBLING, FOREIGN, MISSING = "p-aa", "p-bb", "foreign", "no-such-project"

# The route kinds: spans take an `ingest` key; reads take a `full` key or a session; key
# management takes an owner's session naming the project; /v1/auth/me takes any session.
SPANS, READ, OWNER_ONLY, SIGNED_IN = "spans", "read", "owner", "me"

# credential -> the status each route kind answers.
MATRIX: dict[str, dict[str, int]] = {
    "full key": {SPANS: 202, READ: 200, OWNER_ONLY: 403, SIGNED_IN: 403},
    "full key naming its project": {SPANS: 202, READ: 200, OWNER_ONLY: 403, SIGNED_IN: 403},
    "full key naming a sibling": {SPANS: 403, READ: 403, OWNER_ONLY: 403, SIGNED_IN: 403},
    "full key naming another org": {SPANS: 403, READ: 403, OWNER_ONLY: 403, SIGNED_IN: 403},
    "ingest key": {SPANS: 202, READ: 403, OWNER_ONLY: 403, SIGNED_IN: 403},
    "revoked key": {SPANS: 401, READ: 401, OWNER_ONLY: 401, SIGNED_IN: 403},
    "unknown key": {SPANS: 401, READ: 401, OWNER_ONLY: 401, SIGNED_IN: 403},
    "owner session": {SPANS: 202, READ: 200, OWNER_ONLY: 200, SIGNED_IN: 200},
    "owner session on a sibling": {SPANS: 202, READ: 200, OWNER_ONLY: 200, SIGNED_IN: 200},
    "member session": {SPANS: 202, READ: 200, OWNER_ONLY: 403, SIGNED_IN: 200},
    "session naming no project": {SPANS: 422, READ: 422, OWNER_ONLY: 422, SIGNED_IN: 200},
    "session naming another org": {SPANS: 404, READ: 404, OWNER_ONLY: 404, SIGNED_IN: 200},
    "session naming no such project": {SPANS: 404, READ: 404, OWNER_ONLY: 404, SIGNED_IN: 200},
    "expired session": {SPANS: 401, READ: 401, OWNER_ONLY: 401, SIGNED_IN: 401},
    "logged-out session": {SPANS: 401, READ: 401, OWNER_ONLY: 401, SIGNED_IN: 401},
    "no credentials": {SPANS: 401, READ: 401, OWNER_ONLY: 401, SIGNED_IN: 401},
}


def credentials(api: Api, name: str) -> dict:
    """`Api.call` keyword arguments for one row of the matrix."""
    if name == "no credentials":
        return {}
    if "key" in name:
        if name == "revoked key":
            info = api.store.add_key("revoked-key", PROJECT)
            api.store.revoke_key(PROJECT, info.id)
            return {"key": "revoked-key"}
        key = {"ingest key": "ingest-key", "unknown key": "never-issued"}.get(name, "full-key")
        project = {
            "full key naming its project": OWN,
            "full key naming a sibling": SIBLING,
            "full key naming another org": FOREIGN,
        }.get(name)
        return {"key": key, "project": project}
    owner = api.claim()
    session = api.invite(owner, "member@example.com") if name == "member session" else owner
    if name == "expired session":
        user_id, _ = api.store.sessions[hash_token(session)]
        api.store.sessions[hash_token(session)] = (user_id, datetime.now(UTC) - timedelta(1))
    if name == "logged-out session":
        api.call("POST", "/v1/auth/logout", session=session, status=204)
    project = {
        "session naming no project": None,
        "owner session on a sibling": SIBLING,
        "session naming another org": FOREIGN,
        "session naming no such project": MISSING,
    }.get(name, OWN)
    return {"session": session, "project": project}


def call(api: Api, route: str, **who):
    if route == SPANS:
        return api.spans(**who)
    path = {READ: "/v1/datasets", OWNER_ONLY: "/v1/keys", SIGNED_IN: "/v1/auth/me"}[route]
    return api.call("GET", path, **who)


@pytest.mark.parametrize("name", MATRIX)
def test_the_resolver_matrix(name: str):
    api = Api()
    who = credentials(api, name)
    got = {route: call(api, route, **who).status_code for route in MATRIX[name]}
    assert got == MATRIX[name]


def test_spans_land_in_the_project_the_credential_names():
    api = Api()
    owner = api.claim()
    api.spans(key="full-key", status=202)
    api.spans(key="other-key", status=202)
    api.spans(session=owner, project=SIBLING, status=202)
    assert api.queue.pushed == [PROJECT, OTHER, OTHER]
