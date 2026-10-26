"""Every /v1 route is guarded, apart from the public ones `auth.PUBLIC_ROUTES` names. A new route
that forgets its guard fails here instead of shipping open (design.md, Phase 6).

Each route in the OpenAPI schema is called with no credentials. Guards run before FastAPI
validates the path or body, so a guarded route answers the guard's 401 whatever it's sent."""

import re

from fake_auth import FakeAuth, FakeLimiter
from fake_datasets import FakeDatasets, FakePins
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from agenttrace_ingest.app import create_app
from agenttrace_ingest.auth import NO_CREDENTIALS, PUBLIC_ROUTES
from agenttrace_ingest.settings import Settings

# Fits every path parameter: a UUID, a trace ID, a version number, and a token.
ANY_ID = "0" * 32


def app() -> FastAPI:
    return create_app(
        Settings(),
        checks={},
        auth_store=FakeAuth(),
        limiter=FakeLimiter(),
        datasets=FakeDatasets(),
        pins=FakePins(),
    )


def unguarded(app: FastAPI) -> set[tuple[str, str]]:
    c = TestClient(app)
    found = set()
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/v1"):
            continue
        for method in operations:
            r = c.request(method.upper(), re.sub(r"\{[^}]+\}", ANY_ID, path), json={})
            guarded = r.status_code == 401 and r.json()["detail"] == {"error": NO_CREDENTIALS}
            if not guarded:
                found.add((method.upper(), path))
    return found


def test_only_the_public_routes_are_unguarded():
    assert unguarded(app()) == PUBLIC_ROUTES


def test_a_route_without_a_guard_is_caught():
    a = app()
    forgot = APIRouter()

    @forgot.get("/v1/traces")
    def list_traces() -> dict:
        return {}

    a.include_router(forgot)
    assert unguarded(a) - PUBLIC_ROUTES == {("GET", "/v1/traces")}
