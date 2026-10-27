# AgentTrace

Open-source observability and evals for LLM agents. Sentry plus pytest for AI agents.

- **Trace** every LLM call and tool call your agent makes, on a timeline.
- **Turn failures into tests** with one click.
- **Grade trajectories in CI**: which tools the agent called, in what order, so a prompt change can't quietly break things.

> **Status: Phase 6 (auth).** The Python SDK traces agent runs into the stack, and the web UI lists traces and shows each one's span tree. Any trace can be added to a dataset, and a published version can be run with `agenttrace eval`: it runs your agent over every item, grades outputs and tool-call trajectories, and uploads an experiment you can browse in the UI. Each experiment is compared with its baseline (the run at the merge-base, else the newest on the base branch), and `agenttrace eval --fail-on-regression` or the GitHub Action in [`action.yml`](action.yml) fails a pull request on a statistically real regression and posts the comparison as a comment. An `llm_judge` scorer grades outputs with an Anthropic model. `agenttrace replay` re-runs a failed trace with your current code and scores it against the dataset item made from it. People sign in to the web app; an owner invites members, creates projects, and makes API keys for each one. See [`docs/design.md`](docs/design.md).
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

The first visit asks you to claim the install: open http://localhost:3000/setup and enter the setup token from `docker compose logs ingest` (or the `AGENTTRACE_SETUP_TOKEN` you set in `.env`) with your email and a password. That account is the owner. Invite others from Members; they can use every project, and only owners manage members, projects, and API keys.

Demo agent:

```bash
uv sync --all-packages
uv run demo-agent "Where is order 1042?"
```

Each run is traced into the local stack's default project; open http://localhost:3000/p/default/traces to see it. The SDK reads `AGENTTRACE_API_KEY` and `AGENTTRACE_ENDPOINT` (see `.env.example`). Click "Add to dataset" on a trace to turn it into a test case at http://localhost:3000/p/default/datasets.

Evals: add the demo agent's traces to a dataset named `support`, write each item's expected output or expected tool calls, publish it, then:

```bash
AGENTTRACE_API_KEY=at-local-dev-key uv run agenttrace eval -c examples/demo-agent/eval.yaml
```

It runs the agent 3 times per item, scores each run with the scorers in [`eval.yaml`](examples/demo-agent/eval.yaml), and prints a summary; the experiment, with every run's output and tool calls, is at http://localhost:3000/p/default/experiments.

Replay: to check a fix against the trace that failed, run

```bash
AGENTTRACE_API_KEY=at-local-dev-key uv run agenttrace replay <trace_id> -c examples/demo-agent/eval.yaml --require-pass
```

It calls the agent 3 times with the trace's input and your current code. If the trace was added to the `support` draft and its item has an expectation, each run is scored against it and `--require-pass` exits 3 unless all pass. The trace page lists its replays, and each replay's page shows the runs beside the original with where they first differ.

## API keys and projects

Each API key belongs to one project and has a scope. An `ingest` key only sends spans: give it to agents in production. A `full` key can use the whole project API: `agenttrace eval`, `agenttrace replay`, and the GitHub Action need one. Owners create keys on a project's Settings page, and each key is shown once.

`.env`'s `AGENTTRACE_API_KEY` is a `full` key for the default project, registered by ingest on startup. Its example value, `at-local-dev-key`, is public, so ingest accepts it only while `AGENTTRACE_PUBLIC_URL` is a localhost address.

## Running it for a team

Before you serve it beyond your machine:

- Serve the web app over https, behind a reverse proxy that terminates TLS, and set `AGENTTRACE_PUBLIC_URL` to that address. Session cookies are then marked `Secure`, and ingest stops accepting `at-local-dev-key`.
- Set `AGENTTRACE_API_KEY` in `.env` to a long random key of your own, for example from `openssl rand -base64 32`. Left empty, compose falls back to `at-local-dev-key`, which ingest then refuses, logging an error on every start. Keys made in the web app work alongside it.
- Claim the install right away, ideally with an `AGENTTRACE_SETUP_TOKEN` you chose: until it's claimed, anyone holding the token can.
- Expose only the web app, and ingest's port if agents outside the host send spans. `docker-compose.yml` publishes Postgres, ClickHouse, Redis, and S3 on every interface with the passwords from `.env.example`: change those passwords, and firewall those ports or remove their `ports:` lines.

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
- [`docs/design.md`](docs/design.md): architecture and open questions
- [`docs/eng-log.md`](docs/eng-log.md): engineering log

## License

MIT
