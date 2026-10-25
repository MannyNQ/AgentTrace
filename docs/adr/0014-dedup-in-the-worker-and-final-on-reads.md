# 14. Dedup in the worker, and FINAL on single-trace reads

Status: accepted

## Context

The SDK retries batches and Redis Streams delivers at least once, so the same `(trace_id, span_id)` can arrive more than once (ADR-0003). `ReplacingMergeTree` only collapses duplicates at merge time, and the trace summary view (ADR-0013) counts every insert.

## Decision

The worker drops spans whose `(trace_id, span_id)` is already marked in Redis, and marks spans (`SET NX`, 1-hour expiry) only after their ClickHouse insert succeeds. Reads of one trace's spans use `FINAL`, which is cheap over one trace's bounded range.

## Consequences

- A worker that crashes between inserting and marking can insert a span twice. `spans` collapses it at merge, but the trace summary keeps the extra count. Accepted as a known gap.
- A retry more than an hour after the first delivery isn't caught by the worker. Only `FINAL` reads hide it.
