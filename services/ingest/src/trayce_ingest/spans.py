"""POST /v1/spans: authenticate, validate the whole batch, queue it for the workers (ADR-0017,
ADR-0033)."""

import json
from typing import Annotated, Protocol
from uuid import UUID

import redis
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError

from trayce import Span
from trayce_ingest.auth import Auth, ProjectAccess
from trayce_ingest.settings import Settings

STREAM = "spans"  # the worker's consumer group reads this stream (trayce_worker.main)


class SpanBatch(BaseModel):
    spans: list[Span]


class SpanQueue(Protocol):
    async def push(self, project_id: UUID, spans_json: str) -> None: ...


class RedisQueue:
    def __init__(self, s: Settings):
        self.r = aioredis.Redis.from_url(s.redis_url)
        self.maxlen = s.stream_maxlen

    async def push(self, project_id: UUID, spans_json: str) -> None:
        # One entry per batch (ADR-0015); the oldest entries go first if the cap is reached.
        try:
            await self.r.xadd(
                STREAM,
                {"project_id": str(project_id), "spans": spans_json},
                maxlen=self.maxlen,
                approximate=True,
            )
        except redis.OutOfMemoryError as e:
            # Redis runs with maxmemory and noeviction (docker-compose.yml), so a backlog the
            # workers can't drain refuses new batches instead of taking the host's memory.
            raise HTTPException(503, "the span queue is full; retry later") from e


def router(s: Settings, a: Auth, queue: SpanQueue) -> APIRouter:
    r = APIRouter()
    project = ProjectAccess(a, scope="ingest")  # the one route an `ingest` key may call

    @r.post("/v1/spans", status_code=202)
    async def ingest_spans(request: Request, project_id: Annotated[UUID, Depends(project)]) -> dict:
        body = await request.body()  # size-limited by BodyLimit (app.py)
        try:
            batch = SpanBatch.model_validate_json(body)
        except ValidationError as e:
            # The whole batch is rejected: an invalid span is an SDK bug and should be loud.
            raise HTTPException(422, e.errors(include_url=False, include_input=False)) from e
        if len(batch.spans) > s.max_spans_per_request:
            raise HTTPException(422, f"more than {s.max_spans_per_request} spans in one request")
        if batch.spans:
            spans_json = json.dumps([sp.model_dump(mode="json") for sp in batch.spans])
            await queue.push(project_id, spans_json)
        return {"accepted": len(batch.spans)}

    return r
