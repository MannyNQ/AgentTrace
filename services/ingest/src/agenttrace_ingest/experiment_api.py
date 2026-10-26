"""/v1/experiments: where the eval runner uploads results (ADR-0023). Each route parses, calls
one rule in `experiments`, and returns its record."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from agenttrace.eval.results import Finish, NewExperiment, SampleBatch
from agenttrace_ingest import comparisons as cmp
from agenttrace_ingest import experiments as ex
from agenttrace_ingest.auth import Auth, ProjectAccess

PREFIX = "/v1/experiments"


class ExperimentList(BaseModel):
    experiments: list[ex.ExperimentInfo]


class Uploaded(BaseModel):
    uploaded: int


def router(store: ex.Store, prices: ex.Prices, a: Auth) -> APIRouter:
    r = APIRouter(prefix=PREFIX)
    auth = Depends(ProjectAccess(a))

    @r.get("")
    def list_experiments(
        project: Annotated[UUID, auth], dataset_id: UUID | None = None
    ) -> ExperimentList:
        return ExperimentList(experiments=ex.list_experiments(store, project, dataset_id))

    @r.post("", status_code=201)
    def create(
        project: Annotated[UUID, auth], body: NewExperiment, response: Response
    ) -> ex.ExperimentInfo:
        experiment, created = ex.create(store, project, body)
        if not created:
            response.status_code = 200
        return experiment

    @r.get("/{experiment_id}")
    def detail(project: Annotated[UUID, auth], experiment_id: UUID) -> ex.ExperimentDetail:
        return ex.detail(store, project, experiment_id)

    @r.post("/{experiment_id}/samples")
    def upload(project: Annotated[UUID, auth], experiment_id: UUID, body: SampleBatch) -> Uploaded:
        n = ex.upload_samples(store, prices, project, experiment_id, body.samples)
        return Uploaded(uploaded=n)

    @r.post("/{experiment_id}/finish")
    def finish(
        project: Annotated[UUID, auth], experiment_id: UUID, body: Finish
    ) -> ex.ExperimentInfo:
        return ex.finish(store, project, experiment_id, body)

    @r.get("/{experiment_id}/comparison")
    def comparison(
        project: Annotated[UUID, auth], experiment_id: UUID, baseline: UUID | None = None
    ) -> cmp.Comparison:
        """Against `baseline`, or the one ADR-0007 selects (ADR-0024)."""
        return cmp.compare(store, project, experiment_id, baseline)

    @r.get("/{experiment_id}/items/{item_id}")
    def item(project: Annotated[UUID, auth], experiment_id: UUID, item_id: UUID) -> ex.ItemDetail:
        return ex.item_detail(store, project, experiment_id, item_id)

    return r
