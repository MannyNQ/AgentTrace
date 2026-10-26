import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

from agenttrace_ingest import dataset_api, spans
from agenttrace_ingest.body_limit import BodyLimit
from agenttrace_ingest.dataset_store import PostgresDatasets
from agenttrace_ingest.datasets import Store, TraceInputs
from agenttrace_ingest.migrate import migrate
from agenttrace_ingest.settings import Settings
from agenttrace_ingest.stores import CHECKS, ensure_bucket
from agenttrace_ingest.trace_inputs import StoredTraceInputs

log = logging.getLogger(__name__)


async def run_check(check: Callable[[Settings], None], s: Settings) -> str:
    try:
        await asyncio.wait_for(asyncio.to_thread(check, s), timeout=s.health_timeout_s)
        return "ok"
    except Exception as e:  # health reports every failure as data, never raises
        return f"error: {type(e).__name__}: {e}"


def create_app(
    settings: Settings | None = None,
    checks=CHECKS,
    keys: spans.KeyResolver | None = None,
    queue: spans.SpanQueue | None = None,
    datasets: Store | None = None,
    trace_inputs: TraceInputs | None = None,
) -> FastAPI:
    s = settings or Settings()
    keys = keys or spans.PostgresKeys(s)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # A failed migration stops startup: serving on a half-upgraded schema is worse.
        for name in await asyncio.to_thread(migrate, s.postgres_dsn):
            log.info("schema migration applied: %s", name)
        try:
            await asyncio.to_thread(ensure_bucket, s)
        except Exception:
            log.exception("could not ensure payload bucket; /health will report s3")
        if s.agenttrace_api_key:
            try:
                await asyncio.to_thread(spans.register_key, s, s.agenttrace_api_key)
            except Exception:
                log.exception("could not register AGENTTRACE_API_KEY; ingest will answer 401")
        yield

    app = FastAPI(title="AgentTrace ingest", lifespan=lifespan)
    app.add_middleware(BodyLimit, max_bytes=s.max_request_bytes)
    app.include_router(spans.router(s, keys, queue or spans.RedisQueue(s)))
    app.include_router(
        dataset_api.router(
            datasets or PostgresDatasets(s), trace_inputs or StoredTraceInputs(s), keys
        )
    )
    dataset_api.install_error_handlers(app)

    @app.get("/health")
    async def health(response: Response) -> dict:
        results = await asyncio.gather(*(run_check(c, s) for c in checks.values()))
        stores = dict(zip(checks, results, strict=True))
        ok = all(r == "ok" for r in results)
        response.status_code = 200 if ok else 503
        return {"status": "ok" if ok else "degraded", "stores": stores}

    return app


app = create_app()
