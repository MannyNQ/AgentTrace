# 4. Trace completion: root span end, or a quiet period

Status: accepted

## Context

Workers compute trace summaries (latency, cost, span count, status) once a trace is done, but there is no explicit "trace finished" signal, and agents can crash without closing their root span.

## Decision

A trace is complete when its root span ends. If the root span never ends, the trace is complete after 5 minutes with no new spans. A span that arrives after completion reopens the trace, and its summary is recomputed.

## Consequences

- Trace summaries are eventually consistent; the UI must tolerate a summary changing.
- A crashed agent's trace shows up as complete, with an error status inferred from the unclosed root, about 5 minutes after its last span.
- The quiet period is a setting, not a constant.
