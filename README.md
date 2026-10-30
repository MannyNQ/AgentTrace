# Trayce

Open-source observability and evals for LLM agents. Sentry plus pytest for AI agents.

- **Trace** every LLM call and tool call your agent makes, on a timeline.
- **Turn failures into tests** with one click.
- **Grade trajectories in CI**: which tools the agent called, in what order, so a prompt change can't quietly break things.

> **Status: Phase 8 (Overview).** The Python SDK, or any OpenTelemetry exporter over OTLP, traces agent runs into the stack, and the web UI lists traces and shows each one's span tree. Any trace can be added to a dataset, and a published version can be run with `trayce eval`: it runs your agent over every item, grades outputs and tool-call trajectories, and uploads an experiment you can browse in the UI. Each experiment is compared with its baseline (the run at the merge-base, else the newest on the base branch), and `trayce eval --fail-on-regression` or the GitHub Action in [`action.yml`](action.yml) fails a pull request on a statistically real regression and posts the comparison as a comment. An `llm_judge` scorer grades outputs with an Anthropic model. `trayce replay` re-runs a failed trace with your current code and scores it against the dataset item made from it. People sign in to the web app; an owner invites members, creates projects, and makes API keys for each one. Agents instrumented with OpenTelemetry, such as Pydantic AI, the Vercel AI SDK, or OpenInference instrumentors, send traces to `/otlp/v1/traces`, and `trayce eval` and `trayce replay` capture their tool calls too. Each project opens on an overview of the last 24 hours, 7 days, or 30 days: volume, error rates, latency percentiles, spend by model, and tools, where every chart links to the traces behind it. See [`docs/architecture.md`](docs/architecture.md).
>
> From Phase 2 on, ingest applies database migrations on startup, so an existing dev stack upgrades in place with `docker compose up -d --build`. A stack from before Phase 1 still needs `docker compose down -v` (this deletes its data).

## Quickstart

Needs Docker (or OrbStack), [uv](https://docs.astral.sh/uv/), and pnpm.

```bash
cp .env.example .env            # add ANTHROPIC_API_KEY to run the demo agent
docker compose up -d --wait     # Postgres, ClickHouse, Redis, SeaweedFS, ingest, worker
curl localhost:8000/health      # every store should say "ok"
```

Web UI:

```bash
cd web && pnpm install && pnpm dev   # http://localhost:3000
```

The first visit asks you to claim the install: open http://localhost:3000/setup and enter the setup token from `docker compose logs ingest` (or the `TRAYCE_SETUP_TOKEN` you set in `.env`) with your email and a password. That account is the owner. Invite others from Members; they can use every project, and only owners manage members, projects, and API keys.

Demo agent:

```bash
uv sync --all-packages
uv run demo-agent "Where is order 1042?"
```

Each run is traced into the local stack's default project; open http://localhost:3000/p/default/traces to see it. The SDK reads `TRAYCE_API_KEY` and `TRAYCE_ENDPOINT` (see `.env.example`). Click "Add to dataset" on a trace to turn it into a test case at http://localhost:3000/p/default/datasets.

To trace your own agent, install the SDK from this repo: `pip install './sdk/python[eval]'`. Its PyPI name will be `trayce`, but it isn't published there yet.

Evals: add the demo agent's traces to a dataset named `support`, write each item's expected output or expected tool calls, publish it, then:

```bash
TRAYCE_API_KEY=tr-local-dev-key uv run trayce eval -c examples/demo-agent/eval.yaml
```

It runs the agent 3 times per item, scores each run with the scorers in [`eval.yaml`](examples/demo-agent/eval.yaml), and prints a summary; the experiment, with every run's output and tool calls, is at http://localhost:3000/p/default/experiments.

Replay: to check a fix against the trace that failed, run

```bash
TRAYCE_API_KEY=tr-local-dev-key uv run trayce replay <trace_id> -c examples/demo-agent/eval.yaml --require-pass
```

It calls the agent 3 times with the trace's input and your current code. If the trace was added to the `support` draft and its item has an expectation, each run is scored against it and `--require-pass` exits 3 unless all pass. The trace page lists its replays, and each replay's page shows the runs beside the original with where they first differ.

A replay runs your agent, with its real tools, on what the trace recorded: its input, and for a fork (`--from <span_id>`), the model turns and tool results before the fork point. Treat that as untrusted input. Anyone with an `ingest` key can write a trace, so when the agent's tools can do damage, add `--stub-tools` or replay in a sandbox (ADR-0040).

## OpenTelemetry

Agents already instrumented with OpenTelemetry don't need the SDK to send traces. Point any OTLP/HTTP exporter at ingest with an API key:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:8000/otlp
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer%20<key>
OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT=true
```

An `ingest` key is enough, and creating one on the Settings page shows these lines filled in. The header value is percent-encoded, so the space after `Bearer` is `%20`. Turn content capture on: without it, OpenTelemetry's GenAI instrumentation records no tool arguments, and trajectory scorers that match arguments fail.

Ingest reads OpenTelemetry's GenAI conventions and OpenInference. Model calls, tool calls, agent runs, and retrievals become the matching spans; other spans, such as HTTP requests, are kept as `custom` spans. Cached tokens are taken out of input tokens before pricing, as everywhere else. A span ingest can't store is dropped and reported in the response's `partialSuccess`, rather than failing the batch (ADR-0035). GenAI conventions don't say where an agent run's input goes, so for "Add to dataset" and replay, set `input.value` (with `input.mime_type: application/json` for JSON) on the root span.

`trayce eval` and `trayce replay` install a bridge whenever `opentelemetry-sdk` is installed (`pip install 'trayce[otel]'`), so an OpenTelemetry-instrumented agent's tool spans are graded like the SDK's (ADR-0036). In production, `trayce.otel.install()` sends OpenTelemetry spans through the SDK instead of an OTLP exporter.

[`examples/demo-agent-otel`](examples/demo-agent-otel) is the demo agent on Pydantic AI, traced with OpenTelemetry only. Its model is scripted, so it needs no API key:

```bash
uv run demo-agent-otel "Where is order 1042?"
TRAYCE_API_KEY=tr-local-dev-key uv run trayce eval -c examples/demo-agent-otel/eval.yaml
```

## API keys and projects

Each API key belongs to one project and has a scope. An `ingest` key only sends spans: give it to agents in production. A `full` key can use the whole project API: `trayce eval`, `trayce replay`, and the GitHub Action need one. Owners create keys on a project's Settings page, and each key is shown once.

`.env`'s `TRAYCE_API_KEY` is a `full` key for the default project, registered by ingest on startup. Its example value, `tr-local-dev-key`, is public, so ingest accepts it only while `TRAYCE_PUBLIC_URL` is a localhost address, and warns on every start that it's live. Once the URL isn't local, ingest revokes it on startup, whatever key `.env` names.

## Running it for a team

Before you serve it beyond your machine:

- Serve the web app over https, behind a reverse proxy that terminates TLS, and set `TRAYCE_PUBLIC_URL` to that address. Session cookies are then marked `Secure`, and ingest stops accepting `tr-local-dev-key`. The proxy must append the browser's address to `X-Forwarded-For` (nginx: `$proxy_add_x_forwarded_for`): failed sign-ins are limited per address (ADR-0041). Behind more than one proxy, such as a CDN and nginx, set `TRAYCE_PROXY_HOPS` to how many.
- Set `TRAYCE_API_KEY` in `.env` to a long random key of your own, for example from `openssl rand -base64 32`. Left empty, compose falls back to `tr-local-dev-key`, which ingest then refuses, logging an error on every start. Keys made in the web app work alongside it.
- Claim the install right away, ideally with a `TRAYCE_SETUP_TOKEN` you chose: until it's claimed, anyone holding the token can.
- Expose only the web app, and ingest's port if agents outside the host send spans. `docker-compose.yml` publishes every port on `127.0.0.1` only; set `TRAYCE_INGEST_BIND=0.0.0.0` to publish ingest's. Still change the stores' passwords from the ones in `.env.example`.

## Development

After pulling, rebuild the stack with `docker compose up -d --build`: ingest applies Postgres and ClickHouse migrations when it starts, and the web app's trace pages need them.

```bash
uv run ruff check . && uv run pyright && uv run pytest   # Python
uv run pytest -m integration                             # needs the compose stack
cd web && pnpm lint && pnpm build                        # web
```

## Docs

- [`GLOSSARY.md`](GLOSSARY.md): the domain vocabulary
- [`docs/adr/`](docs/adr/): architecture decisions
- [`docs/architecture.md`](docs/architecture.md): architecture and open questions
- [`docs/eng-log.md`](docs/eng-log.md): engineering log

## License

MIT
