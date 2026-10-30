# Trayce verification map

This directory is the maintained source for verifying Trayce's user-facing behavior. Read this index before driving the app, then use the matching feature file as the recipe. `H` below means `.claude/skills/verify/bin/trayce-verify`.

## Baseline preconditions

- Start a fresh instance with `H up` and wait for `READY`.
- `H doctor` reports `DOCTOR OK`, built from the SHA you mean to verify.
- The instance is the `trayce-verify` compose project on the verify ports: ingest :18000, web :13000. Never drive the dev stack on :8000/:3000.
- Every run writes to `verify-artifacts/<run-id>/`, the directory `up` printed.
- `up` claims the install as `verify-owner@example.com` / `verify-owner-password` (ADR-0034). Every web page but `/status` and the sign-in pages needs a session: in the browser pane, sign in at `http://127.0.0.1:13000/login` with those credentials first. `H traces` uses the same owner's session.
- Web paths in these recipes are inside the default project: `/traces` means `http://127.0.0.1:13000/p/default/traces`. The old paths still work after sign-in, by redirecting into the entity's project (architecture.md, Phase 6).
- The web app reads and writes only through ingest with the signed-in user's session (ADR-0031, ADR-0032). The `curl` recipes below use the `.env` key, which is a `full` key for the default project while the public URL is local (ADR-0033).

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
3. `Driving it with trayce-verify`: starts with `Preconditions:`, then labeled bullets pairing each action with an exact command and an observable result.
4. `Gotchas`: traps that waste or invalidate a run.

## Features

- [Self-host startup](./self-host-startup.md): `docker compose up` from empty volumes seeds the default org and project, the `spans` table, the payload bucket, and the worker's consumer group.
- [Stack health API](./stack-health-api.md): `GET /health` reports each store, returns 503 naming the failed store, and recovers, including the worker reconnecting after a Redis restart.
- [Web health page](./web-health-page.md): the UI shows each store's status, a failed store's real error, and an unreachable ingest API.
- [Span ingest](./span-ingest.md): the SDK and `POST /v1/spans` get spans into ClickHouse with auth, whole-batch validation, payload offload, cost, dedup, and dead-lettering.
- [Trace UI](./trace-ui.md): the trace list and its filters, statuses including `running`, the span tree, span detail, and full payloads.
- [Overview](./overview.md): `/v1/analytics` over 24h, 7d, or 30d in the viewer's zone, the overview page's tiles, charts, and tables with known numbers, drill-downs into the filtered trace list, and the 2 s budget at 1M traces.
- [Datasets](./datasets.md): migrations, the `/v1/datasets` API, Add to dataset from a trace and pinning its trace, item revisions and stale edits, publish rules, version diffs, archive, and expired source traces.
- [Experiments](./experiments.md): the `/v1/experiments` API the eval runner uploads to, with retryable creates and uploads, completion rules, server-side cost, per-scorer and per-item summaries, the `trayce eval` CLI that runs the demo agent and uploads, and the experiment pages: list with a dataset filter, per-scorer and per-item results, and per-sample tool calls with the first mismatch marked.
- [CI gate](./ci-gate.md): comparing an experiment with its merge-base or base-branch baseline, the paired bootstrap and `max_drop`, `trayce eval --fail-on-regression` (exit 3) and `--report-md`, the versus-baseline panel, compare page and side-by-side item view, the `llm_judge` scorer, and `action.yml`.
- [Replay](./replay.md): ClickHouse migrations and `traces.replay_of`, `GET /v1/traces/<id>/input`, the `/v1/replays` API, `trayce replay` with draft-item scoring, `--require-pass` (exit 3, or 2 with nothing to score) and replays of replays, the trace list's "Show replays", the trace page's Replays section, and the replay page with each run's first difference.
- [Auth](./auth.md): claiming an install with the setup token, sign-in and sessions, invites and owner-only pages, projects and scoped API keys, project isolation for keys and sessions, old-path redirects, and a web app without store credentials.
- [OTLP ingest and the OTel bridge](./otlp.md): `POST /otlp/v1/traces` with protobuf, JSON and gzip, translation from the GenAI and OpenInference conventions, `partialSuccess`, 503 while Redis is down, the bridge under `trayce eval` and `replay`, the Pydantic AI `demo-agent-otel`, and the OTLP env lines shown with a new key.
- [Fork](./fork.md): a demo-agent failure recorded with a broken prompt, forked with a fixed one at the step that went wrong; the step changes, no played-back tool runs, and the changed prompt is noted.
- [Demo agent](./demo-agent.md): the Keyly support agent answers through its tools, recovers from tool errors, follows the refund policy, and records full LLM requests and responses and stubbable tool spans.
