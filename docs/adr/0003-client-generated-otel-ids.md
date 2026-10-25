# 3. Client-generated, OTel-format trace and span IDs

Status: accepted

## Context

The SDK sends spans in batches and retries on failure, children often finish before their parents, and teams already on OpenTelemetry should be able to send data without the AgentTrace SDK.

## Decision

The SDK generates IDs on the client in OpenTelemetry format: a 16-byte hex `trace_id` and an 8-byte hex `span_id`. The server never assigns IDs.

## Consequences

- A child span can be sent before its parent ends, since its parent's ID is already known.
- `(trace_id, span_id)` is the deduplication key for retried spans.
- OTLP data maps onto AgentTrace spans without remapping IDs.
