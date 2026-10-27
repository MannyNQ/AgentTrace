# 36. The SDK bridges OpenTelemetry in-process and shares its context

Status: accepted. Builds on ADR-0022 and ADR-0035.

## Context

The eval runner reads a sample's trajectory from the spans the SDK sees in-process, not from the server (ADR-0022). An agent instrumented only with OpenTelemetry sends its spans to an OTel exporter, so under `agenttrace eval` its trajectory would be empty and every trajectory scorer would fail. Its failed traces could reach a dataset through OTLP ingest, but the fix couldn't be tested, which is the loop ADR-0001 exists for.

Agent code can also mix the two: an `@trace` tool called inside a framework's OTel span, or OTel LLM spans under the runner's sample root span. With two separate contexts those would land in different traces.

## Decision

- **Bridge.** An `agenttrace[otel]` extra adds an OTel `SpanProcessor` that runs the ADR-0035 translator on each span as it ends and hands the result to the SDK's span-processor hook and exporter, like any SDK span.
- **When.** `agenttrace eval` and `agenttrace replay` install it automatically whenever `opentelemetry-sdk` is importable. Elsewhere it's opt-in with `agenttrace.otel.install()`.
- **One context.** With the extra installed, the SDK uses OTel's context as its own: every span, ours or OTel's, parents to whichever is current. The runner's sample root span is the OTel parent of the agent's spans, so they share the sample's `trace_id`. Without the extra, the SDK keeps its own `contextvars`.

## Consequences

- An OTel-instrumented agent works with eval and replay unchanged, and its trajectory is whatever its tool spans record (ADR-0022).
- The base package doesn't depend on `opentelemetry-sdk`.
- Sending the same spans through the bridge and an OTLP exporter is harmless: both carry the same `(trace_id, span_id)`, and the worker's dedup keeps one (ADR-0014).
- Translation runs in two places, ingest and the SDK, from the same code in `agenttrace`, so a mapping fix reaches both together but needs an SDK release to reach the bridge.
