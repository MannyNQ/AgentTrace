# 38. One trace filter for the trace list and analytics

Status: accepted. Builds on ADR-0013 and ADR-0031.

## Context

Every chart on the overview links to the trace list filtered to what was clicked, so the two must agree on which traces a filter selects. Most filters are trace-level: tags, user ID, service, status, and whether a trace is an eval or replay run, which only the root span records. The tools, models, and span-kind panels read `spans`, which don't carry those.

## Decision

- One filter type, parsed once in ingest, is taken by `/v1/traces` and every `/v1/analytics/...` endpoint: time range, status, tags, user ID, `service_name`, production versus eval and replay runs, and "has a span of kind K, name N, or model M", optionally errored.
- Span-level queries apply it as `trace_id IN (SELECT trace_id FROM traces WHERE <filter>)`, with the time range on both the subquery and `spans`, so the `spans` sort key still prunes.
- `traces` gains `service_name`, filled by `traces_mv` from the root span's `resource.service.name`, which the SDK also sets, from `TRAYCE_SERVICE_NAME` or `OTEL_SERVICE_NAME`.

## Consequences

- A filter added once appears on both the list and the overview.
- The span contract doesn't change: no trace metadata is copied onto every span.
- The subquery's cost grows with the number of matching traces; the Phase 8 benchmark covers it (ADR-0037).
- Traces stored before the migration have an empty `service_name` and match only "no service".
