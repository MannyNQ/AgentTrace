# AgentTrace

Open-source observability and evals for LLM agents. Sentry plus pytest for AI agents.

- **Trace** every LLM call and tool call your agent makes, on a timeline.
- **Turn failures into tests** with one click.
- **Grade trajectories in CI**: which tools the agent called, in what order, so a prompt change can't quietly break things.

> **Status: Phase 1 (tracing).** The Python SDK traces agent runs into the stack, and the web UI lists traces and shows each one's span tree. See [`docs/design.md`](docs/design.md).
>
> Phase 1 changed the database schemas. A dev stack created before it needs `docker compose down -v` (this deletes its data) before `docker compose up`.
>
> There is no authentication until Phase 6. Don't expose a running instance to the internet.

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

Each run is traced into the local stack; open http://localhost:3000/traces to see it. The SDK reads `AGENTTRACE_API_KEY` and `AGENTTRACE_ENDPOINT` (see `.env.example`).

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
