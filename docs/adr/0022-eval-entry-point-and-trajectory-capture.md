# 22. The eval entry point takes the item input; the runner captures spans in-process

Status: accepted. Builds on ADR-0006. Amended by ADR-0036: OpenTelemetry spans reach the capture hook through the OTel bridge.

## Context

The eval runner (ADR-0006) calls the user's agent once per sample and needs two things back: the output, for output scorers, and the trajectory, for trajectory scorers. Agents have their own signatures (the demo agent's is `run(question: str) -> AgentResult`), and the trajectory exists only as tool spans.

Reading the trajectory back from the server would wait on ingest, Redis, the worker, ClickHouse, and trace completion (ADR-0004), and would need a new trace-read API.

## Decision

- **Entry point.** `eval.yaml` names `module:function`. The runner calls it with the item's `input` as its one argument, unchanged, and the return value is the sample's output. A user whose agent has another signature writes a short adapter. Sync and async functions are both accepted.
- **Samples.** Each sample runs inside a root span the runner opens, so samples running concurrently can't share a trace. Sync entry points run on a thread pool and async ones on one event loop, both bounded by `concurrency` (default 4).
- **Capture.** The SDK gets a span-processor hook that sees every span as it ends, whether or not an exporter is configured. The runner registers one that collects each sample's spans by `trace_id`. The trajectory is that trace's tool spans ordered by start time.
- **Errors.** An exception or exceeding `timeout_s` (default 120) makes an errored sample: every scorer gives it 0 with a reason naming the error. The experiment still completes.

## Consequences

- Scoring needs no server round trip, and a run's traces still reach the server through the normal exporter.
- The trajectory is exactly what the SDK recorded. A tool the agent calls without a tool span is invisible to trajectory scorers, which is also true in production traces.
- Agents that spawn their own threads must propagate `contextvars` for their spans to land in the sample's trace.
