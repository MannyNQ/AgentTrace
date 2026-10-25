# AgentTrace

Open-source observability and evals for LLM agents. Sentry plus pytest for AI agents.

- **Trace** every LLM call and tool call your agent makes, on a timeline.
- **Turn failures into tests** with one click.
- **Grade trajectories in CI**: which tools the agent called, in what order, so a prompt change can't quietly break things.

> **Status: Phase 0 (foundations).** The stack runs and the health check passes; tracing arrives in Phase 1. See [`docs/design.md`](docs/design.md).
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
