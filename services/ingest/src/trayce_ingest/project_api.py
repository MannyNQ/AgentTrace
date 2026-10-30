"""/v1/projects (the org's projects; owners create them), /v1/project (the project a request
acts on; owners rename it or change its retention), and /v1/locate (which project holds a
trace, dataset, experiment, or replay). Each route parses, calls one rule in
`projects`, and returns its record."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from trayce_ingest import auth as au
from trayce_ingest import locate as lc
from trayce_ingest import projects as pr
from trayce_ingest.http import Body
from trayce_ingest.validation import Text

PREFIX = "/v1/projects"
CURRENT = "/v1/project"
LOCATE = "/v1/locate"

ProjectName = Annotated[Text, Field(max_length=100)]


class ProjectList(BaseModel):
    projects: list[au.Project]


class NewProject(Body):
    name: ProjectName


class ProjectPatch(Body):
    name: ProjectName | None = None
    retention_days: int | None = None


def router(a: au.Auth, locations: lc.Store) -> APIRouter:
    r = APIRouter()
    signed_in = Depends(au.SignedIn(a))
    owner = Depends(au.SignedInOwner(a))
    project = Depends(au.ProjectAccess(a))
    owner_project = Depends(au.OwnerProject(a))

    @r.get(PREFIX)
    def list_projects(session: Annotated[au.Session, signed_in]) -> ProjectList:
        """Every project in the user's org; every member sees all of them."""
        return ProjectList(projects=pr.list_projects(a.store, session.user))

    @r.post(PREFIX, status_code=201)
    def create(by: Annotated[au.Owner, owner], body: NewProject) -> au.Project:
        return pr.create(a.store, by, body.name)

    @r.get(CURRENT)
    def current(project_id: Annotated[UUID, project]) -> au.Project:
        """The key's project, or the one a session names; the CLI reads its slug for links."""
        return pr.get(a.store, project_id)

    @r.patch(CURRENT)
    def update(by: Annotated[au.ProjectOwner, owner_project], body: ProjectPatch) -> au.Project:
        return pr.update(a.store, by, body.name, body.retention_days)

    @r.get(LOCATE + "/{kind}/{entity_id}")
    def locate(
        session: Annotated[au.Session, signed_in], kind: lc.Kind, entity_id: str
    ) -> lc.Located:
        """The slug of the project in the user's org that holds this entity."""
        return lc.locate(a.store, locations, session.user, kind, entity_id)

    return r
