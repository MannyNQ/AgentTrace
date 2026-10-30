"""What every router shares: the request body base and the error envelope."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from trayce_ingest.errors import ApiError


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


def install_error_handlers(app: FastAPI) -> None:
    """Every refusal answers `{"detail": {"error": ..., ...}}`. Validation errors use the same
    shape with `fields`, on every route, so none falls back to FastAPI's default, which echoes
    the request (a password, say); a malformed id in the path is a 404."""

    @app.exception_handler(ApiError)
    async def api_error(_: Request, e: ApiError) -> JSONResponse:
        return JSONResponse({"detail": e.detail()}, status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, e: RequestValidationError) -> JSONResponse:
        errors = list(e.errors())
        if any(err["loc"][0] == "path" for err in errors):
            return JSONResponse({"detail": {"error": "not found"}}, status_code=404)
        detail = {"error": "some fields are invalid", "fields": _fields(errors)}
        return JSONResponse({"detail": detail}, status_code=422)
