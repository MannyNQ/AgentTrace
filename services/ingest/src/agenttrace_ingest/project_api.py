"""/v1/projects (the org's projects; owners create them) and /v1/project (the project a request
acts on; owners rename it or change its retention). Each route parses, calls one rule in
`projects`, and returns its record."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from agenttrace_ingest import auth as au
from agenttrace_ingest import projects as pr
from agenttrace_ingest.dataset_api import Body

PREFIX = "/v1/projects"
CURRENT = "/v1/project"

Name = Annotated[str, Field(max_length=100)]


class ProjectList(BaseModel):
    projects: list[au.Project]


class NewProject(Body):
    name: Name


class ProjectPatch(Body):
    name: Name | None = None
    retention_days: int | None = None


def router(a: au.Auth) -> APIRouter:
    r = APIRouter()
    signed_in = Depends(au.SignedIn(a))
    owner = Depends(au.SignedIn(a, owner=True))
    project = Depends(au.ProjectAccess(a))
    owner_project = Depends(au.OwnerProject(a))

    @r.get(PREFIX)
    def list_projects(session: Annotated[tuple[au.User, str], signed_in]) -> ProjectList:
        """Every project in the user's org; every member sees all of them."""
        return ProjectList(projects=pr.list_projects(a.store, session[0]))

    @r.post(PREFIX, status_code=201)
    def create(session: Annotated[tuple[au.User, str], owner], body: NewProject) -> au.Project:
        return pr.create(a.store, session[0], body.name)

    @r.get(CURRENT)
    def current(project_id: Annotated[UUID, project]) -> au.Project:
        """The key's project, or the one a session names; the CLI reads its slug for links."""
        return pr.get(a.store, project_id)

    @r.patch(CURRENT)
    def update(
        caller: Annotated[tuple[au.User, UUID], owner_project], body: ProjectPatch
    ) -> au.Project:
        return pr.update(a.store, caller[1], body.name, body.retention_days)

    return r
