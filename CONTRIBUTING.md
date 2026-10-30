# Contributing

Thanks for helping. Bugs and feature requests go in [issues](https://github.com/MannyNQ/Trayce/issues/new/choose), questions in [Discussions](https://github.com/MannyNQ/Trayce/discussions), and vulnerabilities through private reporting ([`SECURITY.md`](SECURITY.md)). For a change bigger than a fix, open an issue first so we can agree on the shape before you write it.

## Set up

Needs Docker (or OrbStack), Python 3.12+, [uv](https://docs.astral.sh/uv/), and pnpm.

```bash
cp .env.example .env            # add ANTHROPIC_API_KEY to run the demo agent
docker compose up -d --wait     # Postgres, ClickHouse, Redis, SeaweedFS, ingest, worker
curl localhost:8000/health      # every store should say "ok"
uv sync --all-packages
cd web && pnpm install && pnpm dev   # http://localhost:3000
```

Claim the install at http://localhost:3000/setup with the setup token from `docker compose logs ingest`. Then `uv run demo-agent "Where is order 1042?"` sends a trace to the default project. The README's [Develop from source](README.md#develop-from-source) section walks through datasets, `trayce eval`, and `trayce replay` on the demo agent.

After pulling, rebuild the stack with `docker compose up -d --build`. Ingest applies Postgres and ClickHouse migrations when it starts.

## Checks

CI runs these on every pull request. Run them before you push:

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest   # Python
uv run pytest -m integration                                                          # needs the compose stack
cd web && pnpm lint && pnpm build                                                     # web
```

## Read before you change things

- [`GLOSSARY.md`](GLOSSARY.md) fixes the domain vocabulary. Use its terms exactly in code, UI, docs, and issues: a trace, span, dataset item, and experiment mean one thing each, and the words under "Avoid" stay out.
- [`docs/adr/`](docs/adr/) records why things are the way they are. Read the ADRs that touch your change. If your change reverses or extends a decision, add a new ADR or amend the old one in the same pull request.
- [`docs/architecture.md`](docs/architecture.md) shows how ingest, the worker, the stores, the web app, and the SDK fit together.

## How issues are labeled

New issues start with `needs-triage`. A maintainer then moves each one to one of these ([`docs/agents/triage-labels.md`](docs/agents/triage-labels.md)):

| Label | Meaning |
| --- | --- |
| `needs-triage` | A maintainer needs to evaluate it. |
| `needs-info` | Waiting on the reporter for more information. |
| `ready-for-agent` | Fully specified, ready for a coding agent to pick up. |
| `ready-for-human` | Needs a person to implement it. |
| `wontfix` | Won't be done. The closing comment says why. |

`ready-for-human` and `ready-for-agent` issues are open to contributors. Comment on one before you start, so two people don't build the same thing.

## Pull requests

- Keep one change per pull request, with tests for behavior you add or fix.
- Say what changed and why, and link the issue.
- Update the README, `GLOSSARY.md`, or an ADR when the change affects what they say.

By contributing, you agree your work is licensed under the [MIT License](LICENSE).
