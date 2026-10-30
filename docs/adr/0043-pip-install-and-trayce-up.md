# 43. `pip install trayce` and `trayce up`

Status: accepted. Amends ADR-0042 (the distribution is published from the first release on).

## Context

Running Trayce meant cloning the repo, installing Docker, uv, and pnpm, copying `.env`, starting compose, starting the web app separately with `pnpm dev`, and finding the setup token in ingest's logs. Every comparable tool installs with `pip install`. A first user who has to clone a repo before seeing a trace mostly doesn't.

A server with no Docker at all (embedded stores, the web app served from Python) would cost weeks: the stores are ClickHouse, Postgres, Redis, and S3, and the web app uses Next.js server actions, so it can't be exported as static files. That work waits until users say Docker is what stops them.

## Decision

- The `trayce` package is published to PyPI, and the ingest, worker, and web images to `ghcr.io/mannynq/trayce-{ingest,worker,web}`, for linux/amd64 and linux/arm64. A `vX.Y.Z` tag runs `.github/workflows/release.yml`: it checks the tag against `sdk/python/pyproject.toml`, pushes the images tagged `X.Y.Z` and `latest`, and only then publishes to PyPI with trusted publishing. A published package therefore never names images that don't exist.
- `trayce up` runs the stack on this machine with Docker as the only requirement. The package ships `trayce/stack/compose.yml` (the repo's compose file with images in place of builds, the web app added, and only ingest and the web app on host ports) and the SeaweedFS start script. `up` copies both into `TRAYCE_HOME` (default `~/.trayce`) on every run, so an upgraded package runs its own stack. On the first run it writes `.env` with random store passwords, an API key, and a setup token, then prints the URLs, the token, and the SDK and OTLP settings. `trayce down` stops the stack, and `--delete-data` also removes its volumes.
- Images are tagged with the installed package's version. `TRAYCE_VERSION` and `TRAYCE_IMAGE_PREFIX` override that, which is how CI runs `trayce up` on images built from the commit.
- The images need no files from the repo. The Postgres and ClickHouse init SQL in `infra/` becomes migration `0000_base.sql` in each series, applied by ingest. The Postgres one runs only on an empty database: an install made by the init SQL already has those tables, and later migrations changed them (0005 made `projects.slug` NOT NULL), so its inserts would fail there. The ClickHouse one is all `IF NOT EXISTS`. The worker now starts after ingest is healthy, since ingest creates the tables.
- The SeaweedFS start script moves into the package, and the repo's compose file mounts it from there, so there is one copy. `sdk/python/tests/test_stack.py` checks that the two compose files agree on services, store images, environment keys, commands, and dependencies.

## Consequences

- Getting started is `pip install trayce && trayce up`. A user who already has OpenTelemetry instrumentation only has to set two environment variables.
- Docker is still required. If leads say that's the blocker, the next step is the no-Docker server described above, or a hosted endpoint.
- Two compose files have to stay in step. The test catches drift in the structure, but not every value in it, such as healthcheck timings.
- GHCR packages start private. After the first release, each image's visibility has to be set to public once in GitHub's package settings, or `trayce up` fails to pull it.
- The PyPI project and its trusted publisher (this repository, `release.yml`, environment `pypi`) have to be registered on pypi.org before the first tag.
- The ClickHouse migration still runs beside startup rather than before it (it has to, so ingest can accept spans while ClickHouse is down). On a fresh install, a span that arrives in the first moments, before the tables exist, can fail. `trayce up` returns only after every service is healthy, and spans need the key it prints, so in practice nothing is sent that early.
