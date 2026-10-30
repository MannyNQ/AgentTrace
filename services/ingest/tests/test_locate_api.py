"""/v1/locate: which project in the caller's org holds an entity, for old-link redirects."""

from uuid import UUID, uuid4

from fake_auth import FakeAuth, FakeLimiter
from fake_datasets import FakeDatasets, FakePins
from fastapi.testclient import TestClient

from trayce_ingest.app import create_app
from trayce_ingest.auth import Auth
from trayce_ingest.settings import Settings

PROJECT = UUID("00000000-0000-0000-0000-0000000000aa")
OTHER = UUID("00000000-0000-0000-0000-0000000000bb")
FOREIGN = UUID("00000000-0000-0000-0000-0000000000cc")
TRACE = "a" * 32
REPLAY = str(uuid4())


class FakeLocations:
    def __init__(self, held: dict[tuple[str, str], UUID]):
        self.held = held

    def find(self, kind, entity_id, projects):
        found = self.held.get((kind, entity_id))
        return found if found in projects else None


def client() -> tuple[TestClient, str]:
    store = FakeAuth.with_keys({"k": PROJECT, "o": OTHER})
    store.add_project(FOREIGN, "foreign", org_id=uuid4())
    held = {("trace", TRACE): OTHER, ("replay", REPLAY): FOREIGN}
    app = create_app(
        Settings(trayce_setup_token="t"),
        checks={},
        auth_store=store,
        limiter=FakeLimiter(),
        datasets=FakeDatasets(),
        pins=FakePins(),
        locations=FakeLocations(held),
    )
    session = Auth(store, FakeLimiter(), setup_token="t").claim("t", "o@example.com", "o" * 12)
    return TestClient(app, headers={"Authorization": f"Session {session.token}"}), session.token


def test_an_entity_is_located_in_the_users_org():
    c, _ = client()
    assert c.get(f"/v1/locate/trace/{TRACE}").json() == {"slug": "p-bb"}


def test_another_orgs_entity_unknown_ones_and_malformed_ids_look_alike():
    c, _ = client()
    other_org = c.get(f"/v1/locate/replay/{REPLAY}")
    unknown = c.get(f"/v1/locate/replay/{uuid4()}")
    malformed = c.get("/v1/locate/dataset/not-a-uuid")
    assert other_org.status_code == unknown.status_code == malformed.status_code == 404
    assert other_org.json() == unknown.json()
    assert c.get(f"/v1/locate/widget/{TRACE}").status_code == 404


def test_locating_needs_a_session():
    c, _ = client()
    r = c.get(f"/v1/locate/trace/{TRACE}", headers={"Authorization": "Bearer k"})
    assert r.status_code == 403
