# 40. A fork replays a stubbed prefix, then runs live

Status: accepted. Builds on ADR-0028, ADR-0029, ADR-0030, and ADR-0039.

## Context

ADR-0028 deferred fork-from-step and tool stubbing. It pictured a fork as rebuilding the LLM request at step N and continuing from there. That can't continue a real agent: its loop state, counters, and locals at step N lived in a process that is gone, so the most a rebuilt request reaches is one LLM call, which is the playground ADR-0028 already rejected.

## Decision

- A fork calls the user's entry point from the start with the root input, as any replay does. Every LLM call and tool call before the fork point returns its recorded result instead of running; from the fork point on, everything runs live. `agenttrace replay <trace_id> --from <span_id>` starts one; `--stub-tools` answers every tool call from the recording, with or without `--from`.
- Interception happens where the SDK owns the call: the Anthropic integration's `messages.create`, `@trace(kind="tool")`, and `agenttrace.call_tool(name, args, fn)`, for dispatch loops that only know the tool's name at call time. Spans from `span()` blocks, the OTel bridge, OTLP, and streamed calls can't be stubbed; a fork or tool-stubbed replay that needs one is refused before it starts, naming the step.
- Prefix LLM calls are matched by order. Tool calls are matched by name and full JSON equality of arguments, each recording used once, so parallel calls find theirs.
- A prefix LLM request that differs from its recording still gets the stub, and the differing fields are recorded as a warning. A prefix tool call with no match makes the run go live from that step, recorded as `diverged_at`. Under `--stub-tools`, a live-suffix tool call with no match errors the run.
- A tool span is stubbable only if its output survived recording unchanged (it round-trips through JSON) and untruncated; the SDK marks that when the span is recorded.
- A stubbed tool call that errored raises `agenttrace.RecordedToolError` with the recorded type name and message, since the original exception class isn't recorded.
- A fork's `replay_of` points at the fork point in the trace it forked. That trace may itself be a replay run, so forks chain; whole-trace replays still resolve to the original root.
- `replays` gains `fork_span_id` (null for a whole-trace replay) and `tool_mode` (`live` or `recorded`); each run records `diverged_at` and its LLM-request warnings. Scoring is unchanged (ADR-0030).

## Considered options

- **Rebuilding the request at step N.** One live call, not the agent. Rejected for the same reason as the playground.
- **Going live at the first differing LLM request.** A changed system prompt, the most common fix, would differ at step 0 and turn every fork into a whole-trace replay.
- **Running unmatched tools live under `--stub-tools`.** Breaks the one promise the mode makes.

## Consequences

- A fork holds the history fixed and tests whether today's code acts differently at the fork point, which is the question a failed step asks.
- Only agents using the Anthropic integration and `@trace` or `call_tool` tools can be forked. Every agent can still be replayed whole.
- "Replays of trace X" becomes the traces whose `replay_of` chain reaches X.
- First difference still compares whole trajectories, so a divergence in the prefix shows up as the first difference; the UI collapses the stubbed prefix and marks the fork point.
