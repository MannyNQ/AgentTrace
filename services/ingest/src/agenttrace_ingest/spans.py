"""POST /v1/spans: authenticate, validate the whole batch, queue it for the workers (ADR-0017)."""

import hashlib
import json
from typing import Annotated, Protocol
from uuid import UUID

import psycopg
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError

from agenttrace import Span
from agenttrace_ingest.settings import Settings

STREAM = "spans"  # the worker's consumer group reads this stream (agenttrace_worker.main)
DEFAULT_PROJECT_ID = UUID("00000000-0000-0000-0000-000000000001")  # infra/postgres/001_tenancy.sql


class SpanBatch(BaseModel):
    spans: list[Span]


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class KeyResolver(Protocol):
    async def resolve(self, api_key: str) -> UUID | None: ...


class SpanQueue(Protocol):
    async def push(self, project_id: UUID, spans_json: str) -> None: ...


class PostgresKeys:
    """Resolves API keys to projects, caching hits in memory. Keys are never deleted before
    Phase 6, so a cached hit can't go stale."""

    def __init__(self, s: Settings):
        self.s = s
        self.cache: dict[str, UUID] = {}

    async def resolve(self, api_key: str) -> UUID | None:
        h = hash_key(api_key)
        if h in self.cache:
            return self.cache[h]
        async with await psycopg.AsyncConnection.connect(self.s.postgres_dsn) as conn:
            cur = await conn.execute("SELECT project_id FROM api_keys WHERE key_hash = %s", (h,))
            row = await cur.fetchone()
        if row is None:
            return None
        self.cache[h] = row[0]
        return row[0]


def register_key(s: Settings, api_key: str, project_id: UUID = DEFAULT_PROJECT_ID) -> None:
    """Upsert a key for a project. Safe to call on every startup."""
    with psycopg.connect(s.postgres_dsn) as conn:
        conn.execute(
            "INSERT INTO api_keys (key_hash, project_id) VALUES (%s, %s) "
            "ON CONFLICT (key_hash) DO UPDATE SET project_id = EXCLUDED.project_id",
            (hash_key(api_key), project_id),
        )


class RedisQueue:
    def __init__(self, s: Settings):
        self.r = aioredis.Redis.from_url(s.redis_url)
        self.maxlen = s.stream_maxlen

    async def push(self, project_id: UUID, spans_json: str) -> None:
        # One entry per batch (ADR-0015); the oldest entries go first if the cap is reached.
        await self.r.xadd(
            STREAM,
            {"project_id": str(project_id), "spans": spans_json},
            maxlen=self.maxlen,
            approximate=True,
        )


def key_auth(keys: KeyResolver):
    """A dependency resolving the request's Bearer API key to its project (ADR-0017)."""

    async def project(request: Request) -> UUID:
        scheme, _, key = request.headers.get("authorization", "").partition(" ")
        project_id = await keys.resolve(key) if scheme.lower() == "bearer" and key else None
        if project_id is None:
            raise HTTPException(401, "missing or unknown API key")
        return project_id

    return project


def router(s: Settings, keys: KeyResolver, queue: SpanQueue) -> APIRouter:
    r = APIRouter()
    project = key_auth(keys)

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
