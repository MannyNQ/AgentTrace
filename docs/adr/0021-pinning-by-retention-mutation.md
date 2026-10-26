# 21. Pinning rewrites retention in place; only dataset items pin

Status: accepted. Amends ADR-0011 and ADR-0016.

## Context

ADR-0011 pins traces linked from a dataset item or an experiment, and leaves the mechanism to Phase 3. `spans` is a `ReplacingMergeTree(ingested_at)` and `traces` an `AggregatingMergeTree` fed by `traces_mv`, both with TTL `start_time + retention_days` (ADR-0016). Three options:

- Re-insert the spans with a large `retention_days`. The replacing merge drops the old copies in `spans`, but `traces_mv` fires again and the summary's sums (cost, span counts) count the trace twice for good.
- Copy into a `pinned_spans` table with no TTL. Every trace read then unions two tables.
- An `ALTER TABLE ... UPDATE retention_days` mutation. No duplicates and no read changes, but each mutation rewrites the parts it touches.

An experiment produces items × samples traces on every run, so pinning them would keep most of an active project's eval traces forever, and mutations at that rate are too heavy.

## Decision

- Pinning rewrites `retention_days` on the trace's rows in `spans` and `traces` with one `ALTER TABLE ... UPDATE` per table, filtered by `project_id` and `trace_id`, so that each row expires on 2106-01-01. That's the latest date the TTL can reach: it's a `DateTime`, which ends in February 2106 and wraps around to 1970 past it, so a fixed large value such as 65535 days would land a 2026 trace in 2069.
- The ingest API issues it whenever a dataset item gets a `source_trace_id`, once the item is committed and without waiting for the mutation to finish, then sets the item's `pinned_at`. A failed pin is logged and doesn't fail the request: the item holds its own copy of the input.
- On startup, ingest pins the source trace of every item whose `pinned_at` is still null, in the background. That one pass is both the backfill for items created before Phase 3 and the retry for pins that failed; it stops at the first failure and tries again on the next startup.
- Experiments don't pin. Their scores, outputs, and trajectories live in Postgres, which is all a comparison needs; their traces expire on the normal schedule and the UI shows "trace expired".

## Consequences

- Trace reads and the summary view are unchanged.
- Pins are as rare as "Add to dataset", so the mutation cost doesn't matter.
- An item added in the window before the mutation applies can lose its trace only if the trace was already at its expiry, which "source trace expired" already covers.
- Spans that reach ClickHouse after the pin are stamped with the project's retention and expire on schedule. "Add to dataset" needs the root span, which ends the trace (ADR-0004), so this is limited to stragglers.
- Unpinning stays out of scope (ADR-0011): deleting an item doesn't restore the original retention.
