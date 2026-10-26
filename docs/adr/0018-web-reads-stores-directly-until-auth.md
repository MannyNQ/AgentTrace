# 18. The web app reads ClickHouse and S3 directly until auth exists

Status: accepted. Amended by ADR-0024.

## Context

The architecture sketch puts a query API between the web app and the stores. In Phase 1 there's one UI and no auth, so a separate query service would only forward queries.

## Decision

Next.js server components query ClickHouse with `@clickhouse/client`, and a route handler fetches offloaded payloads from S3. Every query is scoped to the default project.

## Consequences

- One fewer service to run in Phase 1.
- Phase 6 revisits this, when auth needs a single place to enforce project access.
