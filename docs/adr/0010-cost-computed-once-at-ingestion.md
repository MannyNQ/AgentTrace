# 10. Cost is computed once at ingestion

Status: accepted

## Context

Model prices change. Recomputing old costs with new prices rewrites history, and treating unknown models as free hides real spend.

## Decision

The worker computes `cost_usd` for each LLM span when it arrives, using the price table entry in effect at the span's start time. The value is stored on the span and never recomputed. A model missing from the price table gets a null cost, not 0.

## Consequences

- Dashboards show what runs actually cost at the time.
- Adding a price later doesn't fill in old spans' costs; a one-off backfill command can, if ever needed.
- Cost charts must show how many spans had a null cost.
