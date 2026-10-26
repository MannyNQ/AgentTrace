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

## Decisions settled in Phase 2

1. **Dataset writes go through the ingest API.** `/v1/datasets` authenticates with API keys like `/v1/spans`. The web app calls it from server actions; the Phase 3 CLI will use the same endpoints (ADR-0019).
2. **Versions are lists of item revisions.** Editing a draft item adds a revision; a version points at one revision per item, so unchanged items are shared between versions. Publishing requires an expectation on every new or changed item (ADR-0020).
3. **Optimistic concurrency.** Every item edit and delete names the revision it was based on. A stale revision gets 409, and the UI says to reload.
4. **Migrations.** Ingest applies numbered SQL files from `agenttrace_ingest/migrations/` on startup, recording each in `schema_migrations` under an advisory lock. Existing stacks upgrade in place; the Phase 0 and Phase 1 init SQL stays as it is.
5. **Add to dataset copies the input.** The server reads the trace's root span from ClickHouse, fetches the full input from S3 when it was offloaded, and copies it into Postgres. The client sends only the trace ID. An input the SDK truncated can't be added.

Also settled:

- Dataset names are slugs (`[a-z0-9][a-z0-9_-]*`, up to 64 characters), unique per project, archived datasets included; a taken name's error points at the dataset holding it. Datasets are addressed by a stable `id`, so they can be renamed. They're archived, never deleted; archived datasets are hidden from the list (until "Show archived") and from the "Add to dataset" picker.
- `POST /v1/datasets` takes an optional `trace_id`, so "Add to dataset" with a new dataset is one request: a trace that can't be copied creates nothing.
- Item contents must be storable in `jsonb`: no NaN or Infinity, and no NUL characters. Those are refused with 422, from the API and from "Add to dataset".
- `expected_trajectory` is a list of `{"tool": "<name>", "args": {...}}` with `args` optional. `args` is a partial match: listed keys must be equal, other keys are ignored. Regex and wildcard matching are left to Phase 3 scorers. `metadata` is a string-to-string map.
- `input` and `expected_output` are each capped at 1 MB of JSON.
- No CSV or JSONL import.
- No pinning yet (ADR-0011, Phase 3). When a linked trace expires, the item keeps its copy of the input and the UI shows "source trace expired".

## Phase 2 plan

A failed trace becomes a dataset item, and a dataset can be published as a version for Phase 3's eval runner:

1. Ingest: migration runner, dataset schema, and the `/v1/datasets` API with its rules.
2. Web: dataset list, dataset page (draft and versions), JSON item editor, publish, version view with what was added, changed, and removed, and "Add to dataset" on the trace page.
3. Proof: API unit tests with a fake store, integration tests against Postgres, ClickHouse and S3, and a `datasets` entry in the verify skill.
