# Span ingest

An agent instrumented with the `agenttrace` SDK sends finished spans in batches to `POST /v1/spans` with its API key. Ingest checks the key, validates the whole batch, and queues it in Redis; the worker dedups, offloads large payloads to S3, prices LLM spans, and writes them to ClickHouse, where the `traces_mv` view keeps a summary per trace.

## Sub-features

- `ingest-accept` answers `202 {"accepted": n}` for a valid batch with a known key.
- `ingest-auth` answers 401 for a missing or unknown key and queues nothing.
- `ingest-validate` answers 422 when any span in the batch is invalid, naming its position, and queues nothing.
- `ingest-size` answers 413 for a body over 5 MB.
- `worker-store` writes every span to `agenttrace.spans` with the key's `project_id` and `retention_days`.
- `worker-offload` moves an input or output over 8 KB to S3 and keeps a 1 KB preview plus `input_uri`/`output_uri`.
- `worker-cost` sets `cost_usd` from `model_prices` for priced models, including prompt-cache reads and writes, and leaves it null for unknown ones.
- `sdk-batching` splits a burst into requests under 4 MB, and sends a span too big for one request truncated and marked `agenttrace.truncated`.
- `sdk-shutdown` exits within about 2 s when ingest is down, dropping what it can't send.
- `worker-dedup` stores a span sent several times exactly once.
- `worker-dead-letter` moves an unparseable stream entry to `spans:dead`.

## How to get to it (user POV)

- Instrument code with `agenttrace.span()`, `@agenttrace.trace`, and `agenttrace.instrument_anthropic()`, with `AGENTTRACE_API_KEY` and `AGENTTRACE_ENDPOINT` set.
- Or POST JSON to `/v1/spans` directly with `Authorization: Bearer <key>`.

## Driving it with agenttrace-verify

Preconditions:

- A healthy run (`H doctor` → `DOCTOR OK`).
- `H` exports `AGENTTRACE_API_KEY` (from `.env`, else `at-local-dev-key`) and points `AGENTTRACE_ENDPOINT` at :18000, so the SDK and compose agree on the key.

- **SDK scenarios.** Run `H emit`. `emit.out` has one JSON line per scenario (`nested`, `large_payload`, `unpriced`, `root_error`, `cached`, `oversized`, `burst`, `duplicate`, `unclosed_root`) with its `trace_id`. Wait about 2 s for the worker, then check the rows below.
- **Stored with project and retention.** Run `H ch ingest-rows "SELECT name, kind, status, project_id, retention_days FROM spans WHERE name LIKE 'verify.%' OR parent_span_id != '' ORDER BY start_time"`. Every row has project `00000000-0000-0000-0000-000000000001` and `30`.
- **Offload.** Run `H ch ingest-offload "SELECT length(input_preview), input_uri FROM spans WHERE name='verify.large-payload'"`. It prints `1024` and `s3://payloads/00000000-…/<trace_id>/<span_id>/input.json`. Then `H traces <large_payload trace_id> trace-large`, open `/api/traces/<trace_id>/spans/<span_id>/input` with curl, and check its length is 20012 bytes (`{"blob": "x…x"}`).
- **Cost.** Run `H ch ingest-cost "SELECT name, model, cost_usd FROM spans WHERE kind='llm' AND model IN ('claude-opus-5-5','not-a-real-model')"`. The opus span from `nested` costs `0.014`; `not-a-real-model` shows `\N`.
- **Cache pricing.** Run `H ch ingest-cache-cost "SELECT cache_read_tokens, cache_write_5m_tokens, cost_usd FROM spans WHERE trace_id='<cached trace_id>' AND kind='llm'"`. It prints `100000	10000	0.084`.
- **Burst split by bytes.** Run `H ch ingest-burst "SELECT count() FROM spans WHERE trace_id='<burst trace_id>'"`. It prints `100` (99 LLM spans and the root): none lost to the 5 MB request limit.
- **Oversized span.** Run `H ch ingest-oversized "SELECT attributes['agenttrace.truncated'], length(input_preview), input_uri != '' FROM spans WHERE name='verify.oversized'"`. It prints `input	1024	1`: truncated by the SDK, still stored, the rest offloaded.
- **Shutdown with ingest down.** Run `H store-stop ingest`, then `/usr/bin/time -p uv run --all-packages python -c "import agenttrace; [agenttrace.span('x').__enter__() for _ in range(500)]" 2>&1 | tail -3`. `real` is under 3 s. Then run `H store-start ingest`.
- **Dedup.** Run `H ch ingest-dedup "SELECT count() FROM spans WHERE name='verify.duplicate'"` → `1`, and `H ch ingest-dedup-summary "SELECT sum(span_count) FROM traces WHERE trace_id='<duplicate trace_id>'"` → `1`.
- **Auth.** Write `{"spans": []}` to the run dir as `empty.json`. `H post ingest-401 '' <file>` and `H post ingest-401-bad nope <file>` end with `http_status=401`; `H post ingest-202 "$AGENTTRACE_API_KEY" <file>` ends with `{"accepted":0}` and `http_status=202`.
- **Validation.** Write a batch with one good span and one with `"kind": "generation"`. `H post ingest-422 "$AGENTTRACE_API_KEY" <file>` ends with `http_status=422`, and its `loc` is `["spans",1,"kind"]`. `XLEN spans` doesn't grow.
- **Dead letter.** Run `docker exec agenttrace-verify-redis-1 redis-cli XADD spans '*' project_id 00000000-0000-0000-0000-000000000001 spans 'not json'`, wait 2 s, then `docker exec agenttrace-verify-redis-1 redis-cli XRANGE spans:dead - +` shows the entry with `reason` `unparseable: …`, and `XPENDING spans workers` shows `0`.

## Gotchas

- Spans arrive about a second after the SDK ends them (exporter flush) plus the worker's read. Poll ClickHouse briefly; don't call a missing row a failure on the first read.
- The SDK sends nothing without `AGENTTRACE_API_KEY`, and logs a single warning. If the emitter's rows never appear, check `emit.err` for that warning or a 401.
- `ReplacingMergeTree` may hold a duplicate until a merge, so the dedup check must use `count()` without `FINAL` to prove the worker skipped it.
- `traces` rows are aggregate states. Always read them with `GROUP BY trace_id` and `sum`/`max`, as the web app does.
