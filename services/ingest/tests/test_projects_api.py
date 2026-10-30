"""/v1/projects and /v1/project with in-memory storage: listing, creating with a slug that never
changes, and owner-only renames and retention changes."""

import pytest
from test_auth_api import Api

from trayce_ingest.projects import slugify


@pytest.fixture
def api() -> Api:
    return Api()


def new_project(api: Api, owner: str, name: str, status=201) -> dict:
    return api.call("POST", "/v1/projects", {"name": name}, session=owner, status=status).json()


@pytest.mark.parametrize(
    "name, slug",
    [
        ("Support Bot (EU)", "support-bot-eu"),
        ("  Café  Agent ", "cafe-agent"),
        ("!!!", "project"),
        ("x" * 60, "x" * 40),
        ("a" * 39 + " b", "a" * 39),
    ],
)
def test_slugs_come_from_names(name: str, slug: str):
    assert slugify(name) == slug


def test_an_owner_creates_a_project_and_everyone_sees_it(api: Api):
    owner = api.claim()
    member = api.invite(owner, "m@example.com")
    made = new_project(api, owner, "Support Bot")
    assert made["slug"] == "support-bot" and made["retention_days"] == 30
    listed = api.call("GET", "/v1/projects", session=member, status=200).json()["projects"]
    assert "support-bot" in [p["slug"] for p in listed]
    api.call("GET", "/v1/datasets", session=member, project="support-bot", status=200)


def test_a_taken_slug_gets_a_number(api: Api):
    owner = api.claim()
    assert new_project(api, owner, "Support Bot")["slug"] == "support-bot"
    assert new_project(api, owner, "support bot!")["slug"] == "support-bot-2"
    assert new_project(api, owner, "Support-Bot")["slug"] == "support-bot-3"


def test_a_taken_name_is_409(api: Api):
    owner = api.claim()
    new_project(api, owner, "Support Bot")
    r = new_project(api, owner, "Support Bot", status=409)
    assert "already exists" in r["detail"]["error"]


def test_only_owners_create_projects(api: Api):
    owner = api.claim()
    member = api.invite(owner, "m@example.com")
    api.call("POST", "/v1/projects", {"name": "x"}, session=member, status=403)
    api.call("POST", "/v1/projects", {"name": "x"}, key="full-key", status=403)
    api.call("GET", "/v1/projects", key="full-key", status=403)  # a key sees only its project
    new_project(api, owner, "   ", status=422)


def test_the_current_project_answers_keys_and_sessions(api: Api):
    owner = api.claim()
    by_key = api.call("GET", "/v1/project", key="full-key", status=200).json()
    by_session = api.call("GET", "/v1/project", session=owner, project="p-aa", status=200).json()
    assert by_key == by_session and by_key["slug"] == "p-aa"


def test_renaming_keeps_the_slug(api: Api):
    owner = api.claim()
    new_project(api, owner, "Support Bot")
    body = {"name": "Helpdesk", "retention_days": 90}
    r = api.call("PATCH", "/v1/project", body, session=owner, project="support-bot", status=200)
    project = r.json()
    assert (project["name"], project["slug"], project["retention_days"]) == (
        "Helpdesk",
        "support-bot",
        90,
    )


def test_retention_is_bounded(api: Api):
    owner = api.claim()
    for days in (0, 3651):
        body = {"retention_days": days}
        api.call("PATCH", "/v1/project", body, session=owner, project="p-aa", status=422)


def test_only_an_owner_session_changes_a_project(api: Api):
    owner = api.claim()
    member = api.invite(owner, "m@example.com")
    body = {"name": "renamed"}
    api.call("PATCH", "/v1/project", body, session=member, project="p-aa", status=403)
    api.call("PATCH", "/v1/project", body, key="full-key", status=403)


def test_renaming_to_a_taken_name_is_409(api: Api):
    owner = api.claim()
    new_project(api, owner, "Support Bot")
    body = {"name": "Support Bot"}
    api.call("PATCH", "/v1/project", body, session=owner, project="p-aa", status=409)


def test_a_project_name_with_a_nul_character_is_422(api: Api):
    owner = api.claim()
    r = new_project(api, owner, "bad\x00name", status=422)
    assert "NUL" in r["detail"]["fields"]["name"]
