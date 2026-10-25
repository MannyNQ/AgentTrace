import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

from agenttrace_ingest.settings import Settings
from agenttrace_ingest.stores import CHECKS, ensure_bucket

log = logging.getLogger(__name__)


async def run_check(check: Callable[[Settings], None], s: Settings) -> str:
    try:
        await asyncio.wait_for(asyncio.to_thread(check, s), timeout=s.health_timeout_s)
        return "ok"
    except Exception as e:  # health reports every failure as data, never raises
        return f"error: {type(e).__name__}: {e}"


def create_app(settings: Settings | None = None, checks=CHECKS) -> FastAPI:
    s = settings or Settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            await asyncio.to_thread(ensure_bucket, s)
        except Exception:
            log.exception("could not ensure payload bucket; /health will report s3")
        yield

    app = FastAPI(title="AgentTrace ingest", lifespan=lifespan)

    @app.get("/health")
    async def health(response: Response) -> dict:
        results = await asyncio.gather(*(run_check(c, s) for c in checks.values()))
        stores = dict(zip(checks, results, strict=True))
        ok = all(r == "ok" for r in results)
        response.status_code = 200 if ok else 503
        return {"status": "ok" if ok else "degraded", "stores": stores}

    return app


app = create_app()
