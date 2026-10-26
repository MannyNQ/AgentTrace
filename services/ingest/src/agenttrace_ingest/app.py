import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Response

from agenttrace_ingest import (
    auth,
    auth_api,
    dataset_api,
    experiment_api,
    experiments,
    locate,
    project_api,
    replay_api,
    replays,
    spans,
    trace_api,
    traces,
)
from agenttrace_ingest.auth_store import PostgresAuth, RedisLimiter
from agenttrace_ingest.body_limit import BodyLimit
from agenttrace_ingest.dataset_store import PostgresDatasets
from agenttrace_ingest.datasets import Pins, Store, TraceInputs, pin_pending
from agenttrace_ingest.experiment_store import PostgresExperiments, PostgresPrices
from agenttrace_ingest.locate import StoredLocations
from agenttrace_ingest.migrate import migrate, migrate_clickhouse
from agenttrace_ingest.pins import ClickHousePins
from agenttrace_ingest.replay_store import PostgresReplays
from agenttrace_ingest.settings import Settings
from agenttrace_ingest.stores import CHECKS, clickhouse_client, ensure_bucket
from agenttrace_ingest.trace_inputs import StoredTraceInputs, TraceRoots
from agenttrace_ingest.trace_store import ClickHouseTraces
from agenttrace_ingest.traces import Traces

log = logging.getLogger(__name__)


async def run_check(check: Callable[[Settings], None], s: Settings) -> str:
    try:
        await asyncio.wait_for(asyncio.to_thread(check, s), timeout=s.health_timeout_s)
        return "ok"
    except Exception as e:  # health reports every failure as data, never raises
        return f"error: {type(e).__name__}: {e}"


def _pin_pending(datasets: Store, pins: Pins) -> None:
    try:
        n = pin_pending(datasets, pins)
    except Exception:
        log.exception("could not look for unpinned dataset traces")
        return
    if n:
        log.info("pinned %d dataset source traces", n)


# Backoff between ClickHouse migration attempts while ClickHouse is unreachable.
CLICKHOUSE_RETRY_FIRST_S = 1.0
CLICKHOUSE_RETRY_MAX_S = 30.0


async def migrate_clickhouse_until_done(s: Settings) -> None:
    """Applies ClickHouse migrations, retrying with backoff until ClickHouse answers. It runs
    beside the app rather than before it: ingest must take spans into Redis while ClickHouse is
    down (ADR-0015), so a ClickHouse outage can't stop it starting. Reads of a column a pending
    migration adds fail until then, as they would while ClickHouse is down anyway."""
    delay = CLICKHOUSE_RETRY_FIRST_S
    while True:
        try:
            names = await asyncio.to_thread(
                migrate_clickhouse, s.postgres_dsn, clickhouse_client(s)
            )
        except Exception:
            log.exception("clickhouse migrations not applied; retrying in %g s", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, CLICKHOUSE_RETRY_MAX_S)
            continue
        for name in names:
            log.info("clickhouse migration applied: %s", name)
        return


def create_app(
    settings: Settings | None = None,
    checks=CHECKS,
    auth_store: auth.Store | None = None,
    limiter: auth.Limiter | None = None,
    queue: spans.SpanQueue | None = None,
    datasets: Store | None = None,
    trace_inputs: TraceInputs | None = None,
    pins: Pins | None = None,
    experiment_store: experiments.Store | None = None,
    prices: experiments.Prices | None = None,
    replay_store: replays.Store | None = None,
    trace_roots: TraceRoots | None = None,
    trace_store: traces.Store | None = None,
    locations: locate.Store | None = None,
) -> FastAPI:
    s = settings or Settings()
    a = auth.Auth(
        auth_store or PostgresAuth(s), limiter or RedisLimiter(s), s.agenttrace_setup_token
    )
    datasets = datasets or PostgresDatasets(s)
    pins = pins or ClickHousePins(s)
    prices = prices or PostgresPrices(s)
    stored_inputs = StoredTraceInputs(s)
    background: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # A failed Postgres migration stops startup: serving on a half-upgraded schema is worse.
        for name in await asyncio.to_thread(migrate, s.postgres_dsn):
            log.info("schema migration applied: %s", name)
        clickhouse = asyncio.create_task(migrate_clickhouse_until_done(s))
        try:
            await asyncio.to_thread(ensure_bucket, s)
        except Exception:
            log.exception("could not ensure payload bucket; /health will report s3")
        if s.agenttrace_api_key:
            try:
                registered = await asyncio.to_thread(
                    a.register_env_key, s.agenttrace_api_key, s.public_url
                )
            except Exception:
                log.exception("could not register AGENTTRACE_API_KEY; ingest will answer 401")
            else:
                if not registered:
                    log.error(
                        "AGENTTRACE_API_KEY is the published dev key and AGENTTRACE_PUBLIC_URL "
                        "(%s) isn't local, so ingest refuses it. Set AGENTTRACE_API_KEY to a key "
                        "of your own.",
                        s.public_url,
                    )
        setup_token = await asyncio.to_thread(a.prepare_setup)
        if setup_token:
            # ADR-0034: whoever can read these logs can claim the install.
            log.warning(
                "no owner yet; claim this install in the web app with setup token %s", setup_token
            )
        # Backfill and retry pins without holding up startup (ADR-0021).
        task = asyncio.create_task(asyncio.to_thread(_pin_pending, datasets, pins))
        background.add(task)
        task.add_done_callback(background.discard)
        yield
        clickhouse.cancel()  # still retrying at shutdown: ClickHouse never came back

    app = FastAPI(title="AgentTrace ingest", lifespan=lifespan)
    app.add_middleware(BodyLimit, max_bytes=s.max_request_bytes)
    app.include_router(spans.router(s, a, queue or spans.RedisQueue(s)))
    app.include_router(auth_api.router(a))
    app.include_router(auth_api.members_router(a))
    app.include_router(auth_api.keys_router(a))
    app.include_router(project_api.router(a, locations or StoredLocations(s)))
    app.include_router(dataset_api.router(datasets, trace_inputs or stored_inputs, pins, a))
    app.include_router(experiment_api.router(experiment_store or PostgresExperiments(s), prices, a))
    app.include_router(
        replay_api.router(
            replay_store or PostgresReplays(s), trace_roots or stored_inputs, prices, a
        )
    )
    trace_reads = Traces(
        trace_store or ClickHouseTraces(s),
        timedelta(seconds=s.trace_quiet_period_s),
        now=lambda: datetime.now(UTC),
    )
    app.include_router(trace_api.router(trace_reads, trace_roots or stored_inputs, a))
    dataset_api.install_error_handlers(
        app,
        (
            dataset_api.PREFIX,
            experiment_api.PREFIX,
            replay_api.PREFIX,
            trace_api.PREFIX,
            auth_api.PREFIX,
            auth_api.MEMBERS_PREFIX,
            auth_api.KEYS_PREFIX,
            project_api.PREFIX,
            project_api.CURRENT,
            project_api.LOCATE,
        ),
    )

    @app.get("/health")
    async def health(response: Response) -> dict:
        results = await asyncio.gather(*(run_check(c, s) for c in checks.values()))
        stores = dict(zip(checks, results, strict=True))
        ok = all(r == "ok" for r in results)
        response.status_code = 200 if ok else 503
        return {"status": "ok" if ok else "degraded", "stores": stores}

    return app


app = create_app()
