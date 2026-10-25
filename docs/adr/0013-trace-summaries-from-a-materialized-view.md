# 13. Trace summaries come from a ClickHouse materialized view; completion is decided at query time

Status: accepted

## Context

The `spans` sort key `(project_id, start_time, trace_id, span_id)` serves "recent traces in a project" but not "all spans of one trace". The trace list also needs trace summaries (ADR-0004), and a late span must reopen a trace and change its summary.

## Decision

A materialized view aggregates every insert into `spans` into a `traces` table (`AggregatingMergeTree`, ordered by `(project_id, trace_id)`) holding each trace's time range, span count, cost, root span fields, and error counts per span kind. The trace list reads `traces`. The trace detail view reads the trace's time range from `traces`, then queries `spans` with `start_time` bounded to it.

Trace completion and status are computed when a trace is read, not stored: a trace is complete when its root span has ended, or when its last span is older than the quiet period. A complete trace whose root never arrived is `error`.

## Consequences

- No worker timers or completion jobs. A late span adds to the aggregate, which is the ADR-0004 "reopen".
- The `spans` sort key stays as drafted.
- The view counts every insert, so a duplicate insert inflates the summary; dedup happens before insert (ADR-0014).
- The quiet period is a setting of the reader (the web app), not the worker.
