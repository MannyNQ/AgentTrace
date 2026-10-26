# 28. A replay re-runs the whole trace on the client

Status: accepted. Builds on ADR-0006 and ADR-0022.

## Context

Phase 5 answers "why did it fail and does my fix work". A replay runs the agent again on a recorded trace's input, with the user's current code, and shows how the new run differs. Three shapes were considered:

- **A server-side playground** that re-sends one LLM call with an edited prompt. The server would need the user's provider keys, which ADR-0006 rules out, and one call isn't the agent: the tools and the loop around them are what fail.
- **Fork from a step.** Inject the recorded prefix (model replies and tool results up to step N) and run live from there. That needs every LLM call recorded in full, and it isn't: an `anthropic.messages.create` span records only `system` and `messages` as input, request parameters as stringified attributes, and tools by name only (`llm.tools`), with no schemas. A prefix rebuilt from that would not be the call the agent made.
- **Re-run the whole trace** from its root span input, through the same entry point the eval runner calls.

## Decision

- `agenttrace replay <trace_id>` calls the user's entry point with the trace's root span input, on the user's machine. The entry point is `eval.yaml`'s (`--config`, default `eval.yaml`), with the same contract as ADR-0022; `--entry module:function` overrides it.
- The CLI reads the input from `GET /v1/traces/{trace_id}/input` in the ingest service, authenticated with API keys (ADR-0017). It uses Add to dataset's copy logic: the root span's input, fetched from S3 when it was offloaded. An input the SDK truncated is refused with 422. The CLI never reads the stores.
- Tools run live, exactly as in `eval`.
- Each run is its own trace, under a root span the runner opens. That root span carries `replay_of`, pointing at the original trace's root span as `(trace_id, span_id)`. The span ID is redundant while replays always start at the root; it's there so fork-from-step can point at a later span without a schema change.
- Any trace whose root span input has arrived untruncated can be replayed, including experiment sample traces and traces still shown as `running`. Replaying a replay resolves to the original's root span, so `replay_of` never chains.

## Consequences

- The server holds no provider keys or tool credentials for replays, and never runs user code.
- A replay tests today's code against a recorded input, not the recorded model replies, so model nondeterminism shows up between runs. Several runs per replay make that visible.
- Live tools repeat their side effects. An agent whose tools write somewhere needs a test setup for replays, as it already does for evals.
- Fork-from-step and stubbing tools with recorded results wait for a later ADR, after LLM spans capture full parameters and tool schemas.
- A trace that has expired can't be replayed. A trace linked from a dataset item is pinned (ADR-0021), so the failures worth fixing stay replayable.
