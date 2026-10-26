"""/v1/replays, where `agenttrace replay` uploads results (ADR-0029), and
`/v1/traces/{trace_id}/input`, where it reads what to re-run (ADR-0028). Each route parses,
calls one rule in `replays`, and returns its record."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from agenttrace.eval.results import Finish, NewReplay, ReplayRunBatch
from agenttrace_ingest import replays as rp
from agenttrace_ingest.datasets import TraceId
from agenttrace_ingest.experiments import Prices
from agenttrace_ingest.spans import KeyResolver, key_auth
from agenttrace_ingest.trace_inputs import RootInput, TraceRoots

PREFIX = "/v1/replays"
TRACES_PREFIX = "/v1/traces"


class ReplayList(BaseModel):
    replays: list[rp.ReplayInfo]


class Uploaded(BaseModel):
    uploaded: int


def router(store: rp.Store, prices: Prices, keys: KeyResolver) -> APIRouter:
    r = APIRouter(prefix=PREFIX)
    auth = Depends(key_auth(keys))

    @r.get("")
    def list_replays(project: Annotated[UUID, auth], trace_id: TraceId) -> ReplayList:
        """Replays of one original trace, newest first."""
        return ReplayList(replays=rp.list_for_trace(store, project, trace_id))

    @r.post("", status_code=201)
    def create(
        project: Annotated[UUID, auth], body: NewReplay, response: Response
    ) -> rp.ReplayInfo:
        replay, created = rp.create(store, project, body)
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


def traces_router(roots: TraceRoots, keys: KeyResolver) -> APIRouter:
    r = APIRouter(prefix=TRACES_PREFIX)
    auth = Depends(key_auth(keys))

    @r.get("/{trace_id}/input")
    def trace_input(project: Annotated[UUID, auth], trace_id: TraceId) -> RootInput:
        """The root span's full input, read as Add to dataset reads it; a truncated input is
        refused with 422."""
        return roots.root(project, trace_id)

    return r
