"""Every guarded route declares one access policy, and each policy's refusals hold on every route
that declares it, not only on the one sample route per kind in test_auth_matrix (#31).

Owner-only rules take an `Owner`, which can't be built for a member, so a route that forgets its
owner guard can't reach them either; the last test pins that."""

import re

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from test_auth_api import Api

from trayce_ingest import auth as au
from trayce_ingest.auth import PUBLIC_ROUTES

ANY_ID = "0" * 32
GUARDS = (au.ProjectAccess, au.SignedIn, au.SignedInOwner, au.OwnerProject)


def policies(api: Api) -> dict[tuple[str, str], object]:
    """(method, path) -> the guard each /v1 and /otlp route depends on."""
    found: dict[tuple[str, str], object] = {}
    for ctx in iter_route_contexts(api.c.app.routes):
        if not isinstance(ctx.route, APIRoute) or not ctx.path.startswith(("/v1", "/otlp")):
            continue
        deps = ctx.route.dependant.dependencies
        guards = [d.call for d in deps if isinstance(d.call, GUARDS)]
        for method in ctx.methods or ():
            assert len(guards) <= 1, f"{method} {ctx.path} has {len(guards)} guards"
            found[method, ctx.path] = guards[0] if guards else None
    return found


def call(api: Api, method: str, path: str, **who):
    return api.call(method, re.sub(r"\{[^}]+\}", ANY_ID, path), json={}, **who)


def test_every_route_but_the_public_ones_declares_a_policy():
    unguarded = {route for route, guard in policies(Api()).items() if guard is None}
    assert unguarded == PUBLIC_ROUTES


def test_every_owner_route_refuses_a_member():
    api = Api()
    owner = api.claim()
    member = api.invite(owner, "member@example.com")
    owner_routes = [
        r for r, g in policies(api).items() if isinstance(g, au.SignedInOwner | au.OwnerProject)
    ]
    assert len(owner_routes) >= 8  # members, invites, resets, keys, projects
    for method, path in owner_routes:
        r = call(api, method, path, session=member, project="p-aa")
        assert r.status_code == 403, (method, path, r.text)


def test_every_full_project_route_refuses_an_ingest_key():
    api = Api()
    routes = [
        r
        for r, g in policies(api).items()
        if isinstance(g, au.ProjectAccess | au.OwnerProject)
        and getattr(g, "scope", "full") == "full"
    ]
    for method, path in routes:
        r = call(api, method, path, key="ingest-key")
        assert r.status_code == 403, (method, path, r.text)


def test_every_signed_in_route_refuses_an_api_key():
    api = Api()
    routes = [r for r, g in policies(api).items() if isinstance(g, au.SignedIn | au.SignedInOwner)]
    for method, path in routes:
        r = call(api, method, path, key="full-key")
        assert r.status_code == 403, (method, path, r.text)


def test_an_owner_can_t_be_made_from_a_member():
    api = Api()
    owner = api.claim()
    api.invite(owner, "member@example.com")
    member = next(u for u in api.store.users(au.DEFAULT_ORG_ID) if u.role == "member")
    with pytest.raises(au.Forbidden):
        au.Owner(member)
