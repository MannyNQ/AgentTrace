# 2. Org and project scoping from day one, auth later

Status: accepted. Phase 6 auth: ADR-0031 to ADR-0034.

## Context

Multi-tenancy and auth are planned for Phase 6. Spans live in ClickHouse, where adding a column to the sort key later means rewriting the table.

## Decision

Every span, dataset, and experiment carries a `project_id` from the first schema, and every query filters by it. Orgs and projects exist as Postgres entities from Phase 0. A self-hosted install creates one default org and one default project. User accounts, roles, and auth wait until Phase 6.

## Consequences

- `project_id` leads the ClickHouse sort key from the start.
- Before Phase 6, anyone who can reach the server can read all data. The README must say so.
