# OTLP ingest and the OTel bridge

Agents instrumented only with OpenTelemetry send traces to `POST /otlp/v1/traces` (OTLP/HTTP, protobuf or JSON, gzipped or not) with an API key, `ingest` scope being enough. Ingest translates each span into an AgentTrace span from OpenTelemetry's GenAI conventions or OpenInference and queues it like `/v1/spans`, so storage, cost, and trace completion don't change; a span it can't store is dropped and reported in `partialSuccess` (ADR-0035). In-process, the `agenttrace[otel]` bridge does the same translation and shares OpenTelemetry's context, and `agenttrace eval` and `agenttrace replay` install it, so an OTel-only agent's tool spans are its trajectory (ADR-0036). `examples/demo-agent-otel` is that agent: Pydantic AI with a scripted `FunctionModel`, no model key needed. A new key's settings view shows the OTLP env lines.

## Sub-features

- `otlp-ingest` `POST /otlp/v1/traces`: protobuf or JSON, gzip, answered in the request's encoding; 200 with `partialSuccess.rejectedSpans` and the first reason for spans with a bad or all-zero ID or no start time; 400 for a body that can't be decoded or is over 5 MB after decompression or 1000 spans; 401 without a valid key; 415 for another content type; 503 with `Retry-After` while Redis is down.
- `otlp-translate` kinds by `gen_ai.operation.name` or `openinference.span.kind`; a tool span named for its tool with its arguments as input; `input_tokens` without cached tokens, cost from the response model; messages and tool calls in payloads; `input.value`/`output.value` on spans with no other content; session and user IDs and `resource.*` attributes on the root; `agenttrace.convention` on every span; a remote parent stored as a root.
- `otlp-bridge` eval and replay install the bridge; OTel spans nest under the runner's sample span; an OTel LLM span directly under an SDK LLM span (the Anthropic SDK's own span inside `instrument_anthropic()`'s) is dropped, not counted twice.
- `otlp-demo` `uv run demo-agent-otel "<question>"` exports over OTLP and prints the trace ID; `examples/demo-agent-otel/eval.yaml` runs it under `agenttrace eval` and `agenttrace replay`.
- `otlp-key-env` creating a key shows an "Environment" box: the SDK lines for every key, plus `OTEL_EXPORTER_OTLP_ENDPOINT=<endpoint>/otlp`, `OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer%20<key>`, and `OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT=true` for an `ingest` key.

## How to get to it (user POV)

- Any OpenTelemetry exporter with `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:18000/otlp` and `OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer%20<key>`.
- The demo: `uv run demo-agent-otel "<question>"`, then `uv run agenttrace eval -c examples/demo-agent-otel/eval.yaml` and `uv run agenttrace replay <trace_id> -c examples/demo-agent-otel/eval.yaml`.
- The web app: `/settings` → "API keys" → Create key, for the env lines; the trace pages, for what arrived.
- The API: `curl -X POST http://127.0.0.1:18000/otlp/v1/traces -H "Authorization: Bearer <key>" -H 'content-type: application/json' --data-binary @export.json`.

## Driving it with agenttrace-verify

Preconditions:

- A healthy run. No model key needed, except for the dedup check, which runs the demo agent's evals. `$FULL` is the `.env` key, and `$INGEST` an `ingest` key made with the owner's session: `curl -X POST http://127.0.0.1:18000/v1/keys -H "Authorization: Session <token from verify-artifacts/.state/cookies.txt>" -H "AgentTrace-Project: default" -H 'content-type: application/json' -d '{"name":"otlp","scope":"ingest"}'` (the `key` field).

- **Real exporter.** `uv run python .claude/skills/verify/bin/otlp_send.py http://127.0.0.1:18000 "$INGEST"` prints `$T`; ingest's log has `POST /otlp/v1/traces … 200` per span. `H ch otlp "SELECT name, kind, status, error_type, model, input_tokens, cache_read_tokens, cost_usd, session_id, attributes['resource.service.name'] FROM spans WHERE trace_id='$T' ORDER BY start_time"`: the root `invoke_agent support` (`agent`, session `conv-verify-1`, service `otlp-verify-bot`); `chat claude-sonnet-5-5` (`llm`, 500 input tokens from 1500 less 1000 cached, `cost_usd` 0.0016 at 2/10/0.2 per Mtok); `lookup_order` (`tool`, `error`, `OrderNotFound`, input `{"order_id": "1042"}`). Every span has `agenttrace.convention` = `gen_ai`. `H traces $T` shows the agent, llm, and tool tree with `✗ OrderNotFound`. `GET /v1/traces/$T` with `$INGEST` is 403.
- **Partial success.** Post JSON with three spans in one trace: one good, one with `spanId` `0000000000000000`, one with `spanId` `zzzz`. The answer is 200 `{"partialSuccess": {"rejectedSpans": "2", "errorMessage": "span 0000000000000000: invalid span ID … (and 1 more)"}}` and only the good span is stored.
- **Redis down.** `H store-stop redis`, post the same body: `503`, `retry-after: 5`, `can't queue spans`. `H store-start redis` and it's 200 again.
- **The demo agent over OTLP.** `env -u OTEL_EXPORTER_OTLP_HEADERS AGENTTRACE_API_KEY="$INGEST" OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:18000/otlp uv run demo-agent-otel "Can I get a refund on order 1044?"` prints the outside-the-window answer and `trace $T2`. Its spans: root `demo-agent-otel` (`custom`, input `{"question": …}`, service `demo-agent-otel`), `invoke_agent support` (`agent`), three `chat scripted-support` (`llm`, tokens, no cost: the model has no price), `lookup_order`, and `issue_refund` (`error`).
- **Add to dataset, eval, replay.** Seed `support` with `python3 -I .claude/skills/verify/bin/seed_support_dataset.py http://127.0.0.1:18000 "$FULL"`. `POST /v1/datasets/<id>/items/from-trace` with `$T2` copies `{"question": …}` into a draft item; PATCH it (rev 1) to `expected_output: "30-day"` and `expected_trajectory: [{tool: lookup_order, args: {order_id: "1044"}}]`. With `AGENTTRACE_API_KEY="$FULL" AGENTTRACE_ENDPOINT=http://127.0.0.1:18000`: `uv run agenttrace eval -c examples/demo-agent-otel/eval.yaml` ends `completed 12 samples over 4 items`, `answer 6/6`, `steps 9/12`, `few-steps 12/12`, exit 0, with every trajectory from bridged OTel spans; `uv run agenttrace replay $T2 -c examples/demo-agent-otel/eval.yaml` is `scored against item … rev 2`, all three scorers `3/3 pass`, exit 0, and each run's trace has the tool spans `lookup_order, issue_refund`.
- **No double-counted LLM calls.** Needs `ANTHROPIC_API_KEY`: `uv run agenttrace eval -c examples/demo-agent/eval.yaml` gives the experiments entry's results, and `H ch llm "SELECT name, has(mapKeys(attributes), 'agenttrace.convention') AS bridged, count() FROM spans WHERE kind='llm' GROUP BY name, bridged"` has only `anthropic.messages.create` with `bridged` 0; the experiment's cost equals the spans' summed `cost_usd`.
- **Key env lines.** In the browser pane at `/settings`, create a key named `otel agent` with scope `ingest`: the "Environment" box shows the key in `AGENTTRACE_API_KEY` and `OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer%20<key>`, and the endpoint lines. Screenshot it. Source only its three `OTEL_*` lines and run `env -u AGENTTRACE_API_KEY uv run demo-agent-otel "Where is order 1042?"`: the trace arrives (ingest's log has the 200, ClickHouse the spans). A `full` key's box has no `OTEL_` lines.

## Gotchas

- `OTEL_EXPORTER_OTLP_HEADERS` values are percent-encoded: the space after `Bearer` must be `%20`.
- `OTEL_EXPORTER_OTLP_ENDPOINT` is the base, `…/otlp`; the exporter adds `/v1/traces`. The traces-specific `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` is used as given, so it needs the full `…/otlp/v1/traces`.
- An `ingest` key can send spans but not read them: read back with `$FULL` or `H ch`.
- The demo CLI defaults its headers from `AGENTTRACE_API_KEY` only when `OTEL_EXPORTER_OTLP_HEADERS` is unset; a stale one in the shell wins.
- The Environment box's endpoint is `AGENTTRACE_ENDPOINT` as the web server sees it. `H` exports the verify port, so it reads `http://127.0.0.1:18000` there.
