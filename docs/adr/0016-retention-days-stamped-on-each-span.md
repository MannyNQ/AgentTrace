# 16. Per-project retention is a column stamped on each span

Status: accepted

## Context

ADR-0011 calls for per-project retention enforced with a ClickHouse TTL, but a TTL expression can't read Postgres, where `projects.retention_days` lives. The alternative, scheduled `ALTER TABLE ... DELETE` mutations per project, is heavy and needs a scheduler.

## Decision

`spans` and `traces` carry a `retention_days` column, stamped by the worker from the project at ingestion. The TTL is `toDateTime(start_time) + toIntervalDay(retention_days)`.

## Consequences

- Changing a project's retention applies to new spans only.
- Pinning (Phase 3, ADR-0011) still works by copying into a table without a TTL.
