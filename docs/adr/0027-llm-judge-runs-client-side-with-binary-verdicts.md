# 27. The LLM judge runs client-side, gives binary verdicts, and stays out of the agent's trace

Status: accepted. Builds on ADR-0006.

## Context

Some outputs can't be graded by string or schema checks. An LLM judge needs a provider key, which ADR-0006 keeps off the server. Judges are noisy, and a judge that changes model or fails during a provider outage can look like an agent regression.

## Decision

- `type: llm_judge` runs in the eval runner with the user's `ANTHROPIC_API_KEY`. Other providers go through `custom` scorers.
- Config: `model` (required, no default), `criteria` (required, a plain-language rubric), and optional `include`, a subset of `input`, `output`, `expected_output`, `trajectory` (default: input, output, and expected output when the item has one). All of it is part of the `eval.yaml` hash, so changing the judge resets baselines.
- The judge returns a pass or fail verdict and a reason as structured output (`output_config.format` with a JSON schema), so the reply always parses. The score is 1 or 0, and the reason is kept on a pass too. Forced tool use (`tool_choice` of type `tool`) was the first plan, but current models (Opus 5.5, Sonnet 5.5, Fable 5.1) refuse it with a 400.
- A judge API error is retried 3 times (the Anthropic client's `max_retries`); after that, or on a refusal or a reply cut short, the scorer is skipped for that sample, not scored 0. A skip has no score row, so its reason is printed on the CLI's line for that sample rather than stored.
- Judge calls share the run's `concurrency` limit and run outside the sample's trace with auto-instrumentation suppressed, so they never appear in the agent's trace, trajectory, or cost.
- Each sample uploads the judge's token counts per model separately from the agent's. The server computes `judge_cost_usd` from the price table on arrival (ADR-0010, ADR-0023).

## Consequences

- Binary verdicts fit the pass-rate metric (ADR-0025) and are more consistent than a numeric scale.
- A provider outage shrinks a scorer's denominator instead of faking a regression.
- Eval cost is reported as agent cost plus judge cost.
