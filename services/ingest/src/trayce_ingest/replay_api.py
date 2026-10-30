"""/v1/replays, where `trayce replay` uploads results (ADR-0029); it reads what to re-run
from `/v1/traces/{trace_id}/input` (`trace_api`). Each route parses, calls one rule in
`replays`, and returns its record."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from trayce.eval.results import Finish, NewReplay, ReplayRunBatch
from trayce_ingest import replays as rp
from trayce_ingest.auth import Auth, ProjectAccess
from trayce_ingest.datasets import TraceId
from trayce_ingest.experiments import Prices
from trayce_ingest.trace_inputs import TraceRoots

PREFIX = "/v1/replays"


class ReplayList(BaseModel):
    replays: list[rp.ReplayInfo]


class Uploaded(BaseModel):
    uploaded: int


def router(store: rp.Store, roots: TraceRoots, prices: Prices, a: Auth) -> APIRouter:
    r = APIRouter(prefix=PREFIX)
    auth = Depends(ProjectAccess(a))

    @r.get("")
    def list_replays(project: Annotated[UUID, auth], trace_id: TraceId) -> ReplayList:
        """Replays of one original trace, newest first."""
        return ReplayList(replays=rp.list_for_trace(store, project, trace_id))

    @r.post("", status_code=201)
    def create(
        project: Annotated[UUID, auth], body: NewReplay, response: Response
    ) -> rp.ReplayInfo:
        replay, created = rp.create(store, roots, project, body)
        if not created:
            response.status_code = 200
        return replay

    @r.get("/{replay_id}")
    def detail(project: Annotated[UUID, auth], replay_id: UUID) -> rp.ReplayDetail:
        return rp.detail(store, project, replay_id)

    @r.post("/{replay_id}/runs")
    def upload(project: Annotated[UUID, auth], replay_id: UUID, body: ReplayRunBatch) -> Uploaded:
        return Uploaded(uploaded=rp.upload_runs(store, prices, project, replay_id, body.runs))

    @r.post("/{replay_id}/finish")
    def finish(project: Annotated[UUID, auth], replay_id: UUID, body: Finish) -> rp.ReplayInfo:
        return rp.finish(store, project, replay_id, body)

    return r
