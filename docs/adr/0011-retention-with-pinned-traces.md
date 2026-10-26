# 11. Retention with pinned traces

Status: accepted. Amended by ADR-0021: experiments don't pin, and pinning rewrites retention in place.

## Context

A self-hosted install shouldn't fill its disk, but traces that dataset items or experiments link to are the evidence behind tests (ADR-0001) and must not disappear.

## Decision

Spans expire after a per-project retention period, 30 days by default, enforced with a ClickHouse TTL. A trace linked from a dataset item's `source_trace_id` or from an experiment is pinned and kept indefinitely.

## Consequences

- A ClickHouse TTL can't consult Postgres to see which traces are linked. Pinning is therefore a write: when a trace gets linked, its spans are copied to a pinned table with no TTL (or rewritten with a far-future expiry). The approach is settled in Phase 3.
- Unlinking doesn't unpin; pinned traces are only removed by an explicit delete.
