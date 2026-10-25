# AgentTrace verification map

This directory is the maintained source for verifying AgentTrace's user-facing behavior. Read this index before driving the app, then use the matching feature file as the recipe. `H` below means `.claude/skills/verify/bin/agenttrace-verify`.

## Baseline preconditions

- Start a fresh instance with `H up` and wait for `READY`.
- `H doctor` reports `DOCTOR OK`, built from the SHA you mean to verify.
- The instance is the `agenttrace-verify` compose project on the verify ports: ingest :18000, web :13000. Never drive the dev stack on :8000/:3000.
- Every run writes to `verify-artifacts/<run-id>/`, the directory `up` printed.

## Driving conventions

- Start every recipe from the baseline. A recipe that stops a service ends by starting it again and re-running `H doctor`.
- Treat commands as literal. Keep quoted strings and flags unchanged.
- Name each capture after the feature and state (`health-redis-down`), so artifacts read on their own.
- Prefer stable handles: route paths, JSON keys, store labels on the page, accessible names in the browser pane.
- Read the web page with `H page` for text. Use the browser pane only for screenshots, and see the hidden-pane gotcha in `SKILL.md`.

## Proof and skip reporting

- Capture the user action and the resulting state, not only the final screen.
- API proof is the `.cmd` (command and exit code) plus the `.out` body including `http_status`.
- Web proof is a `page` summary plus a browser screenshot of the same state.
- Startup proof includes the stored rows, tables and buckets, read back through `docker exec` or the S3 API.
- Record the feature ID and entry point for every artifact.
- Report an unreachable path with the attempted command and the unmet precondition. Never report a skipped entry point as verified through a different path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior, then exactly four H2 sections in this order:

1. `Sub-features`: short IDs, one line each.
2. `How to get to it (user POV)`: every user entry point.
3. `Driving it with agenttrace-verify`: starts with `Preconditions:`, then labeled bullets pairing each action with an exact command and an observable result.
4. `Gotchas`: traps that waste or invalidate a run.

## Features

- [Self-host startup](./self-host-startup.md): `docker compose up` from empty volumes seeds the default org and project, the `spans` table, the payload bucket, and the worker's consumer group.
- [Stack health API](./stack-health-api.md): `GET /health` reports each store, returns 503 naming the failed store, and recovers, including the worker reconnecting after a Redis restart.
- [Web health page](./web-health-page.md): the UI shows each store's status, a failed store's real error, and an unreachable ingest API.
- [Span ingest](./span-ingest.md): the SDK and `POST /v1/spans` get spans into ClickHouse with auth, whole-batch validation, payload offload, cost, dedup, and dead-lettering.
- [Trace UI](./trace-ui.md): the trace list, statuses including `running`, the span tree, span detail, and full payloads.
- [Demo agent](./demo-agent.md): the Keyly support agent answers through its tools, recovers from tool errors, and follows the refund policy.
