# 21. Pinning rewrites retention in place; only dataset items pin

Status: accepted. Amends ADR-0011 and ADR-0016.

## Context

ADR-0011 pins traces linked from a dataset item or an experiment, and leaves the mechanism to Phase 3. `spans` is a `ReplacingMergeTree(ingested_at)` and `traces` an `AggregatingMergeTree` fed by `traces_mv`, both with TTL `start_time + retention_days` (ADR-0016). Three options:

- Re-insert the spans with a far-future `retention_days`. The replacing merge drops the old copies in `spans`, but `traces_mv` fires again and the summary's sums (cost, span counts) count the trace twice for good.
- Copy into a `pinned_spans` table with no TTL. Every trace read then unions two tables.
- An `ALTER TABLE ... UPDATE retention_days` mutation. No duplicates and no read changes, but each mutation rewrites the parts it touches.

An experiment produces items × samples traces on every run, so pinning them would keep most of an active project's eval traces forever, and mutations at that rate are too heavy.

## Decision

- Pinning sets `retention_days = 65535` (the UInt16 maximum, about 179 years) on the trace's rows in `spans` and `traces` with one `ALTER TABLE ... UPDATE` per table, filtered by `project_id` and `trace_id`.
- The ingest API issues it whenever a dataset item gets a `source_trace_id`, without waiting for the mutation to finish. A failed pin is logged and doesn't fail the request: the item holds its own copy of the input.
- A one-time migration pins traces that items created before Phase 3 already link to.
- Experiments don't pin. Their scores, outputs, and trajectories live in Postgres, which is all a comparison needs; their traces expire on the normal schedule and the UI shows "trace expired".

## Consequences

- Trace reads and the summary view are unchanged.
- Pins are as rare as "Add to dataset", so the mutation cost doesn't matter.
- An item added in the window before the mutation applies can lose its trace only if the trace was already at its expiry, which "source trace expired" already covers.
- Unpinning stays out of scope (ADR-0011): deleting an item doesn't restore the original retention.
