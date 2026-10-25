# AgentTrace design doc (v1)

AgentTrace is a self-hostable, open-source platform for teams building LLM agents. It answers three questions: what did my agent do (tracing), why did it fail and does my fix work (replay), and did my change break anything else (evals in CI).

Vocabulary is defined in [`GLOSSARY.md`](../GLOSSARY.md). Lasting decisions are in [`docs/adr/`](adr/). This doc ties them together.

## Angle

Failures become tests, and tests grade trajectories (ADR-0001). A failed production trace becomes a dataset item in one click; the CI eval then checks which tools the agent called, in what order, not just its final answer.

## Architecture

```
 agent + SDK ──spans (batched, async)──▶ ingest API ──▶ Redis Streams ──▶ workers
                                                                            │
          ┌─────────────────────────────────────────────────────────────────┤
          ▼                          ▼                                      ▼
   Postgres                    ClickHouse                         S3 (SeaweedFS locally)
   orgs, projects,             spans: append-only,                payloads > 8 KB
   datasets, experiments       queried in aggregate

 web (Next.js) ──▶ query API ──▶ Postgres + ClickHouse + S3
 agenttrace CLI (eval runner, runs on the client) ──▶ results upload
```

| Store | Holds | Why |
|---|---|---|
| Postgres | Orgs, projects, datasets, experiments | Relational data that changes and needs transactions |
| ClickHouse | Spans | Append-only, high volume, read as aggregates (latency percentiles, cost by model) |
| Redis Streams | Spans in flight | Ingest returns immediately; a database outage doesn't lose spans |
| S3 | Payloads over 8 KB | Keeps ClickHouse rows small so analytics stay fast (ADR-0009) |

Key constraints from the ADRs:

- Every row is scoped by `project_id` from day one; auth arrives in Phase 6 (ADR-0002).
- The SDK generates OTel-format IDs, so `(trace_id, span_id)` is the dedup key (ADR-0003).
- A trace is complete when its root span ends or after a 5-minute quiet period (ADR-0004).
- The eval runner runs on the client; the server never executes user code (ADR-0006).
- Cost is computed once at ingestion (ADR-0010).

## Repo layout

| Path | What |
|---|---|
| `sdk/python/` | `agenttrace` package. Phase 0: the span contract (`Span`, `SpanKind`, ID helpers) |
| `services/ingest/` | FastAPI ingest API. Phase 0: `/health` across all four stores |
| `services/worker/` | Redis Streams consumer. Phase 0: joins the `workers` group and acks |
| `web/` | Next.js UI. Phase 0: a service health page |
| `examples/demo-agent/` | Customer-support agent with 5 mock tools; the test fixture for every later phase |
| `infra/` | ClickHouse and Postgres init SQL, SeaweedFS S3 config |

## Decisions settled in Phase 1

1. **Sort key and trace lookup.** `spans` keeps `(project_id, start_time, trace_id, span_id)`. A materialized view fills a `traces` summary table; trace completion and status are computed at query time (ADR-0013).
2. **Dedup.** The worker skips `(trace_id, span_id)` pairs already marked in Redis; single-trace reads use `FINAL` (ADR-0014).
3. **Redis Streams.** At-least-once delivery, `XAUTOCLAIM` after 60 s idle, a `spans:dead` stream after 5 deliveries, `MAXLEN ~ 1000000` (ADR-0015).
4. **Per-project retention.** A `retention_days` column stamped at ingestion drives the TTL (ADR-0016). Pinning is settled in Phase 3.
5. **Ingest wire format.** JSON only, whole-batch validation, API keys resolve the project (ADR-0017). OTLP comes later.

Also settled: the web app reads ClickHouse and S3 directly until Phase 6 (ADR-0018); spans carry separate `input_uri` and `output_uri`; prices live in a Postgres `model_prices` table; the SDK sends each span when it ends and does nothing without an API key.

## Phase 1 plan

The demo agent's runs appear in the UI:

1. SDK: `@trace` decorator, `span()` context manager, `contextvars` propagation, background batching exporter, Anthropic client auto-instrumentation.
2. Ingest: `POST /v1/spans` (JSON), API-key auth stub resolving to the default project, push to Redis.
3. Worker: validate, offload payloads, compute cost, batch insert into ClickHouse.
4. Web: trace list and a basic trace tree.
