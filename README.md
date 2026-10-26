# AgentTrace

Open-source observability and evals for LLM agents. Sentry plus pytest for AI agents.

- **Trace** every LLM call and tool call your agent makes, on a timeline.
- **Turn failures into tests** with one click.
- **Grade trajectories in CI**: which tools the agent called, in what order, so a prompt change can't quietly break things.

> **Status: Phase 2 (datasets).** The Python SDK traces agent runs into the stack, and the web UI lists traces and shows each one's span tree. Any trace can be added to a dataset, and a dataset's draft can be published as an immutable version for the Phase 3 eval runner. See [`docs/design.md`](docs/design.md).
>
> From Phase 2 on, ingest applies database migrations on startup, so an existing dev stack upgrades in place with `docker compose up -d --build`. A stack from before Phase 1 still needs `docker compose down -v` (this deletes its data).
>
> There is no authentication until Phase 6. Don't expose a running instance to the internet.
>
> Until then the web app manages datasets through the ingest API with an API key from its server environment: `AGENTTRACE_API_KEY`, else the same variable in the repo-root `.env` that compose reads, else `at-local-dev-key`. The key never reaches the browser, but anyone who can open the web app can use it through the UI.

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

Demo agent:

```bash
uv sync --all-packages
uv run demo-agent "Where is order 1042?"
```

Each run is traced into the local stack; open http://localhost:3000/traces to see it. The SDK reads `AGENTTRACE_API_KEY` and `AGENTTRACE_ENDPOINT` (see `.env.example`). Click "Add to dataset" on a trace to turn it into a test case at http://localhost:3000/datasets.

## Development

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
