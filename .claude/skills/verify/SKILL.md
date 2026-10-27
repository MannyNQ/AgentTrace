---
name: verify
description: Launch and drive a real, isolated AgentTrace instance (docker compose stack, ingest API, worker, Next.js web UI, demo-agent CLI) to prove behavior end to end and capture evidence. Use after changing anything in services/, infra/, web/, sdk/, examples/demo-agent/, or docker-compose.yml, or whenever you need to show a feature works in the running app rather than in unit tests.
---

# Verify AgentTrace

Drive a throwaway AgentTrace instance the way a user would, and keep proof. Everything goes through one helper:

```bash
.claude/skills/verify/bin/agenttrace-verify <command>
```

Run it from the repo root. Commands: `up`, `doctor`, `health [name]`, `page [name]`, `store-stop <svc>`, `store-start <svc>`, `agent "<question>" [name]`, `emit [name]`, `post <name> <key> <file>`, `ch <name> "<sql>"`, `traces [trace_id] [name]`, `web-restart`, `down`.

The feature map in [`features/README.md`](features/README.md) lists what to verify and how. Read it, pick the features your change touches, and drive every entry point listed there, not just the convenient one.

## What the user touches

| Surface | Where | Driven by |
|---|---|---|
| Self-hosted stack | `docker compose up` (6 services) | `up`, `doctor`, `docker exec` for side effects |
| Ingest HTTP API | `GET /health`, `POST /v1/spans`, `POST /otlp/v1/traces`, `/v1/datasets`, `/v1/experiments` (with `/comparison`), `/v1/replays`, `/v1/traces/<id>/input`, `/v1/analytics/<part>` | `health`, `post` (curl), `emit` (real SDK) |
| Web UI | sign-in at `/login`, `/setup`, `/invite/<token>`, `/reset/<token>`; the public health page at `/status`; per project under `/p/<slug>/`: the overview at `overview` (where `/` lands), traces at `traces` and `traces/<id>`, `datasets`, `experiments`, comparisons at `experiments/<id>/compare/<baseline>`, replays at `replays/<id>`, and `settings` (project and API keys); `/projects` and `/members`. Paths without `/p/<slug>/` redirect into the entity's project | `page` (`/status`), `traces` (server HTML, with the owner's session) + the built-in browser pane |
| Demo agent CLI | `uv run demo-agent "<question>"`, traced into the instance; `uv run demo-agent-otel "<question>"`, over OTLP | `agent`; the otlp recipe |
| Stored state | ClickHouse `spans`/`traces`, Postgres datasets, Redis streams, S3 payloads | `ch`, `docker exec` |

`H` exports `AGENTTRACE_API_KEY` (from `.env`, else `at-local-dev-key`) and `AGENTTRACE_ENDPOINT=http://127.0.0.1:18000`, so compose registers the same key the SDK sends and every agent run is traced into this instance, never the dev stack. The web server runs with `TRACE_QUIET_PERIOD_S=20`.

## Isolation

The helper runs a separate compose project, `agenttrace-verify`, with its own fresh volumes, on these ports:

| Service | Verify port | Dev default |
|---|---|---|
| ingest | 18000 | 8000 |
| web (`next start`) | 13000 | 3000 |
| Postgres | 15432 | 5432 |
| ClickHouse HTTP / native | 18123 / 19000 | 8123 / 9000 |
| Redis | 16379 | 6379 |
| S3 (SeaweedFS) | 18333 | 8333 |

The web build goes to `web/.next-verify` (`NEXT_DIST_DIR`), so it never collides with a running `next dev`. The user's dev stack (`-p agenttrace`, default ports) can keep running alongside, and you must not touch it.

Only one verification instance can run at a time, since the ports are fixed. `up` refuses to start if any verify port is taken or a run is already active. Never "fix" that by killing whatever holds the port. Run `doctor` to see whether it's yours. If it isn't, stop and report.

## Launch

```bash
.claude/skills/verify/bin/agenttrace-verify up
```

What it does, in order (about 25 s warm, a few minutes on a first image build):

1. Refuses if a run is active or any verify port is in use.
2. Copies `.env.example` to `.env` if `.env` is missing.
3. Creates `verify-artifacts/<run-id>/` and records the git SHA.
4. Runs `docker compose -p agenttrace-verify up -d --build --wait`. Fresh volumes mean Postgres and ClickHouse run their init SQL. "Healthy" for ingest means its `/health` passed, which requires the default project row, the `spans` table, Redis, and the `payloads` bucket, not just open ports.
4a. Claims the install (ADR-0034) with the setup token from ingest's logs, as owner `verify-owner@example.com` / `verify-owner-password`, and keeps that session in `verify-artifacts/.state/cookies.txt` for `traces`. Sign in with the same credentials in the browser pane. Start with `CLAIM=0 up` to test the setup page on an unclaimed install; `traces` then refuses to run.
5. Builds the web app (`next build`) into `.next-verify`, restoring `web/tsconfig.json` afterwards because the build rewrites it.
6. Starts `next start` on 127.0.0.1:13000 in its own process group, with only `INGEST_URL=http://127.0.0.1:18000` and `AGENTTRACE_PUBLIC_URL=http://127.0.0.1:13000`: no store credentials or API key (ADR-0031).

It's ready when it prints `READY run=<id> ingest=http://127.0.0.1:18000 web=http://127.0.0.1:13000 artifacts=<dir>`. A non-zero exit prints the tail of the failing log, and the full logs are in the run dir (`compose-up.log`, `web-build.log`, `web.log`).

Set `RUN_ID=<name>` before `up` to name the run dir instead of using a timestamp.

## Doctor

```bash
.claude/skills/verify/bin/agenttrace-verify doctor
```

This is read-only. Run it first, and again whenever anything looks off. It prints:

- The active run and the SHA it was built from versus HEAD. If they differ, your change isn't in the running instance: run `down` then `up`.
- Health of each `agenttrace-verify-*` container.
- That ingest is published on :18000 by our project.
- `GET /health` status.
- That the :13000 listener's process group is the one `up` started.

It ends with `DOCTOR OK` or `DOCTOR FAIL` (exit 1). Don't drive a `DOCTOR FAIL` instance. Read the failing line, check `docker logs agenttrace-verify-<svc>-1`, fix, then `down` and `up`.

## Drive

Each command writes `<name>.cmd` (command and exit code), `<name>.out` and `<name>.err` to the run dir, then prints the result. Pass a name to keep several captures apart (`health health-redis-down`).

- **API health:** `agenttrace-verify health [name]` curls `/health`. The body is followed by `http_status=<code>`.
- **Web page, as text:** `agenttrace-verify page [name]` saves the full server-rendered HTML of `/status` and writes `<name>.summary` with one `label => result` line per store, or `Ingest API unreachable`.
- **Web page, visually:** use the built-in browser pane. Navigate to `http://127.0.0.1:13000/status`, find the heading `Service health` with `find`, then take a `screenshot`. Every other page needs a session: sign in at `/login` first. Copy the screenshot file the tool reports into the run dir with a descriptive name.
- **Fault injection:** `agenttrace-verify store-stop <svc>` and `store-start <svc>`, where svc is `postgres`, `clickhouse`, `redis`, `seaweedfs` or `ingest`. These only touch the verify project. `store-start` waits for the service to be healthy again.
- **Demo agent:** `agenttrace-verify agent "Where is order 1042?"` runs the CLI and captures `agent.{cmd,out,err}`. It needs `ANTHROPIC_API_KEY` in `.env`. Each run calls the real API and costs money.
- **Side effects:** check stored state directly, read-only.
  - `docker exec agenttrace-verify-postgres-1 psql -U agenttrace -d agenttrace -tAc "<sql>"`
  - `docker exec agenttrace-verify-clickhouse-1 clickhouse-client --user agenttrace --password agenttrace -q "<sql>"`
  - `docker exec agenttrace-verify-redis-1 redis-cli <cmd>`

## Evidence

Proof goes in `verify-artifacts/<run-id>/`, which is gitignored and survives `down`. A proof includes:

- The action and the resulting state: the command with its exit code and output, not just a final screen.
- For the web UI: the `page` summary and a browser screenshot, both from the same state.
- Side effects checked from a second view. Startup proof is the rows and tables themselves (`docker exec … psql`), not only a green health check.
- For fault paths: the state before, during and after recovery (for example `health`, `health-redis-down`, `health-recovered`).

Rules:

- Exercise the real user path: `docker compose`, HTTP, the page, the CLI. Never call Python functions directly or use test-only hooks as proof.
- Don't mock anything. The only external system is the Anthropic API, and the demo agent either uses it for real or the journey is reported as not verified.
- A report names each feature ID and entry point it covered, and lists anything skipped with the unmet precondition.

## Cleanup

```bash
.claude/skills/verify/bin/agenttrace-verify down
```

This kills only the process group `up` started, runs `docker compose -p agenttrace-verify down -v` (only our project and our volumes), removes `web/.next-verify` and `verify-artifacts/.state`, and keeps `verify-artifacts/<run-id>/`. Run it after every run, including failed ones. Never `docker compose down` without `-p agenttrace-verify`, and never kill by process name: either would take out the user's dev stack or dev server.

Afterwards, `lsof -nP -iTCP:13000 -sTCP:LISTEN` and `docker ps -a --filter name=agenttrace-verify` should both come back empty.

## Gotchas

- **The browser pane shows "Checking services…" forever.** React reveals streamed Suspense content on an animation frame, which a hidden pane never runs. Take a `screenshot` (it forces a frame) or use `page`, which reads the full server response. This isn't an app bug.
- **A stale build.** `up` builds images and the web app from the working tree at that moment. After editing services, SDK, or compose files, run `down` then `up`. After editing only `web/`, `web-restart` rebuilds and restarts the web app and keeps the stack and its data. `doctor` shows the SHA it was built from.
- **Missing API key.** Without `ANTHROPIC_API_KEY`, `agent` fails with `TypeError: Could not resolve authentication method` before any tool runs. Report the demo-agent journey as not verified, and don't swap in a fake client.
- **First runs are slow.** Building images and pulling ClickHouse can take minutes. `up` waits up to 240 s for health.
