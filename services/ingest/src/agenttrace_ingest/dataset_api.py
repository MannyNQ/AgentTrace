"""/v1/datasets: the one way to read and write datasets (ADR-0019). Each route parses, calls one
rule in `datasets`, and returns its record; the records are the response schemas."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from agenttrace_ingest import datasets as ds
from agenttrace_ingest.auth import Auth, ProjectAccess
from agenttrace_ingest.datasets import Contents, Name, Text, TraceId

PREFIX = "/v1/datasets"


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewDataset(Body):
    name: Name
    description: Text = ""
    trace_id: TraceId | None = None  # start the draft with this trace (Add to dataset)


class DatasetPatch(Body):
    name: Name | None = None
    description: Text | None = None
    archived: bool | None = None


class ItemEdit(Body):
    rev: int  # the revision this edit was based on
    contents: Contents


class FromTrace(Body):
    trace_id: TraceId


class DatasetList(BaseModel):
    datasets: list[ds.Dataset]


class VersionList(BaseModel):
    versions: list[ds.VersionInfo]


class Published(BaseModel):
    version: int


def router(store: ds.Store, traces: ds.TraceInputs, pins: ds.Pins, a: Auth) -> APIRouter:
    r = APIRouter(prefix=PREFIX)
    auth = Depends(ProjectAccess(a))

    @r.get("")
    def list_datasets(
        project: Annotated[UUID, auth], include_archived: bool = False
    ) -> DatasetList:
        return DatasetList(datasets=store.list_datasets(project, include_archived))

    @r.post("", status_code=201)
    def create_dataset(project: Annotated[UUID, auth], body: NewDataset) -> ds.Dataset:
        if body.trace_id is None:
            return ds.create_dataset(store, project, body.name, body.description)
        return ds.create_from_trace(
            store, traces, pins, project, body.name, body.description, body.trace_id
        )

    @r.get("/{dataset_id}")
    def get_dataset(project: Annotated[UUID, auth], dataset_id: UUID) -> ds.Dataset:
        return ds.get_dataset(store, project, dataset_id)

    @r.patch("/{dataset_id}")
    def update_dataset(
        project: Annotated[UUID, auth], dataset_id: UUID, body: DatasetPatch
    ) -> ds.Dataset:
        return ds.update_dataset(
            store, project, dataset_id, body.name, body.description, body.archived
        )

    @r.get("/{dataset_id}/items")
    def draft(project: Annotated[UUID, auth], dataset_id: UUID) -> ds.Draft:
        return ds.draft(store, project, dataset_id)

    @r.post("/{dataset_id}/items", status_code=201)
    def add_item(project: Annotated[UUID, auth], dataset_id: UUID, body: Contents) -> ds.Item:
        return ds.add_item(store, project, dataset_id, body)

    @r.patch("/{dataset_id}/items/{item_id}")
    def edit_item(
        project: Annotated[UUID, auth], dataset_id: UUID, item_id: UUID, body: ItemEdit
    ) -> ds.Item:
        return ds.edit_item(store, project, dataset_id, item_id, body.rev, body.contents)

    @r.delete("/{dataset_id}/items/{item_id}", status_code=204)
    def delete_item(
        project: Annotated[UUID, auth], dataset_id: UUID, item_id: UUID, rev: int
    ) -> Response:
        ds.delete_item(store, project, dataset_id, item_id, rev)
        return Response(status_code=204)

    @r.post("/{dataset_id}/items/from-trace", status_code=201)
    def add_from_trace(
        project: Annotated[UUID, auth], dataset_id: UUID, body: FromTrace
    ) -> ds.Item:
        return ds.add_from_trace(store, traces, pins, project, dataset_id, body.trace_id)

    @r.post("/{dataset_id}/publish")
    def publish(project: Annotated[UUID, auth], dataset_id: UUID) -> Published:
        return Published(version=ds.publish(store, project, dataset_id))

    @r.get("/{dataset_id}/versions")
    def versions(project: Annotated[UUID, auth], dataset_id: UUID) -> VersionList:
        return VersionList(versions=ds.versions(store, project, dataset_id))

    @r.get("/{dataset_id}/versions/{n}")
    def version(project: Annotated[UUID, auth], dataset_id: UUID, n: int) -> ds.VersionDiff:
        return ds.diff_version(store, project, dataset_id, n)

    return r


def _fields(errors: list[dict[str, Any]]) -> dict[str, str]:
    """Validation errors by top-level field. An item edit's fields sit under `contents`."""
    fields: dict[str, str] = {}
    for e in errors:
        loc = list(e["loc"][1:])  # drop "body" / "query"
        if loc[:1] == ["contents"]:
            loc = loc[1:]
        key = str(loc[0]) if loc else "request"
        rest = ".".join(str(p) for p in loc[1:])
        fields.setdefault(key, f"{rest}: {e['msg']}" if rest else e["msg"])
    return fields


def install_error_handlers(app: FastAPI, prefixes: tuple[str, ...]) -> None:
    """Dataset errors, which the experiment rules raise too, answer `{"detail": {"error": ...,
    ...}}`. Validation errors on routes under `prefixes` use the same shape with `fields`; a
    malformed id in the path is a 404."""

    @app.exception_handler(ds.DatasetError)
    async def dataset_error(_: Request, e: ds.DatasetError) -> JSONResponse:
        return JSONResponse({"detail": e.detail()}, status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, e: RequestValidationError) -> JSONResponse:
        if not request.url.path.startswith(prefixes):
            return await request_validation_exception_handler(request, e)
        errors = list(e.errors())
        if any(err["loc"][0] == "path" for err in errors):
            return JSONResponse({"detail": {"error": "not found"}}, status_code=404)
        detail = {"error": "some fields are invalid", "fields": _fields(errors)}
        return JSONResponse({"detail": detail}, status_code=422)
