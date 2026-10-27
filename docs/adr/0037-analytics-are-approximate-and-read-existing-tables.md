# 37. Analytics are approximate and read the existing tables

Status: accepted. Builds on ADR-0013, ADR-0014, and ADR-0031.

## Context

The overview aggregates a project's spans over up to 30 days: volume, error rates, latency percentiles, cost and tokens by model, and per-tool numbers. Exact answers would need `FINAL` (or a dedup subquery) on every aggregate, because the worker's dedup (ADR-0014) can let a duplicate through, and exact percentiles, which hold every value in memory. Pre-aggregated rollups would be faster, but a rollup can't take back a duplicate it already counted, and trace completion and status are decided at read time (ADR-0013), so a rollup can't know them.

## Decision

- `/v1/analytics/...` reads `spans` and `traces` directly, without `FINAL`, and computes percentiles with `quantileTDigest`.
- The overview labels its numbers approximate. Exact accounting stays with experiments and comparisons, which gate.
- No rollup tables or projections until the Phase 8 benchmark (30d in under 2 s at 1M traces and 20M spans) is missed. Adding one amends this ADR.

## Consequences

- A rare duplicate span overcounts by one; the trace list and the overview can differ by the same amount, since the list reads summaries the same way.
- Trace-level numbers group a project's `traces` rows by `trace_id`, whose sort key isn't time, so they scan the project's summaries. The benchmark measures this first.
- Cost stays as computed at ingestion (ADR-0010); analytics never reprices.
