# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

**Primary: the agent engineer.** A senior backend or ML engineer who owns an LLM agent in production (a support bot, a coding agent, an internal ops agent) and writes Python. We usually meet them right after they shipped a regression: a prompt or model change passed their spot checks, and the agent quietly started skipping a step. They're between incidents, evaluating tools, and they read config files and CLI output before marketing copy. They distrust hype, eval scores that move on noise, and tools that need their data on someone else's servers before they've decided to trust them.

**Secondary: the engineering lead or platform owner.** Responsible for several agents and the people changing them. Cares about the CI gate, who can see what (owners, members, API key scopes), and running inside their own network.

**Inside the app:** the same engineers, mid-task: reading a trace to see what the agent did, building a dataset from failures, checking why an experiment regressed, comparing a replay with the original.

## Product Purpose

AgentTrace is open-source tracing and evals for LLM agents ("Sentry plus pytest for AI agents"). It records every LLM call and tool call an agent makes as a span tree, turns a failed trace into a dataset item in one click, and grades the agent's trajectory (which tools it called, in what order) in CI. A prompt or model change can't quietly break the agent.

Success: an engineer changes a prompt and knows before merging whether the agent got worse, and every failure they've seen in production stays a test the agent has to pass.

## Positioning

One loop no single neighboring feature covers: **failure → test → gate**, with the trajectory as the thing under test. A production failure becomes a dataset item. `agenttrace eval` runs the agent over every item and compares the result with the run at the merge-base. The PR fails only on a statistically real regression: a paired item-level bootstrap with a per-scorer `max_drop` (ADR-0025), not a dip in an average. `agenttrace replay` then re-runs the failed trace with the fix and shows where the runs first differ. Tracing alone and evals alone are table stakes; the loop is the product.

## Operating Context

- Local stack: `docker compose up -d --wait` (Postgres, ClickHouse, Redis, SeaweedFS, ingest, worker), web UI at `localhost:3000` from `web/`, claimed with a setup token.
- Agents send spans through the Python SDK, or any OpenTelemetry exporter over OTLP/HTTP to `/otlp/v1/traces` with an API key.
- Evals run on the engineer's machine or in CI: `agenttrace eval -c eval.yaml`, `--fail-on-regression`, and the GitHub Action in `action.yml`, which posts the comparison as a PR comment.
- The fixture for everything: the demo agent in `examples/demo-agent/` ("Where is order 1042?") and its OTel twin in `examples/demo-agent-otel/`.
- Evaluation happens against other agent observability and eval tools; buyers run the quickstart and read the license and repository before deciding.

## Capabilities and Constraints

- Tracing: spans of kind `llm`, `tool`, `agent`, `retrieval`, `custom`, with tokens, cost, and latency; full LLM requests and responses (ADR-0039). OTLP ingest reads OpenTelemetry GenAI conventions and OpenInference (Pydantic AI, Vercel AI SDK, OpenInference instrumentors).
- Datasets: one-click "Add to dataset", editable draft, immutable published versions, linked traces pinned past retention.
- Evals: scorers include `contains`, `trajectory` (`in_order`, `any_order`, `exact`, `forbidden`, `max_steps`), and `llm_judge` (an Anthropic model, binary verdicts). Experiments compared with their baseline.
- Replay: re-runs a trace with current code, scores it against its dataset item, shows the first difference; forks replay a stubbed prefix (ADR-0040).
- Project overview: volume, error rates, latency percentiles, spend by model, and tools, each chart linking to its traces.
- Team access: owners and members, invites, projects, API keys scoped `ingest` or `full`.
- The eval runner runs on the client; the server never executes user code (ADR-0006).
- SDK is Python only.
- Terminology is fixed by `GLOSSARY.md`; use its terms exactly (trace, span, LLM span, tool span, trajectory, dataset, dataset version, item, experiment, scorer, replay).
- **Undecided:** hosted or cloud offering, pricing, PyPI package name (`agenttrace` on PyPI belongs to a different project), public docs site, domain, final logo assets.

## Brand Commitments

- Brand authority: `docs/brand/toolkit.md`. Its voice rules (section 5), lexicon, claims guardrails (section 6), and naming rules (section 8, "Logo") are binding.
- Voice: precise, blunt, calm, generous. Sentence case, second person, no emoji, no exclamation marks, no hype words.
- Name: "AgentTrace" (one word, capital A and T); CLI and package `agenttrace` in lowercase monospace.
- MIT-licensed, open source, runs on the user's own infrastructure. Never imply a hosted service, price, compliance, or performance numbers, and never characterize a competitor.
- Visual direction is open. Toolkit section 8 is background only, not a constraint: the public site need not match the app's zinc neutrals, and the first build in that direction (the "Route Diagram" page) was judged too basic. Marketing surfaces should commit at full strength: a dense, product-specific drawing owning the first viewport, color committed at page scale.

## Evidence on Hand

- Real span trees from `uv run demo-agent "Where is order 1042?"`.
- Real eval config: `examples/demo-agent/eval.yaml`; real `agenttrace eval` output and experiment pages from running it.
- The PR comparison comment produced by `action.yml`.
- Replay pages showing the first difference between runs.
- OTLP setup lines (README, "OpenTelemetry"); the Settings page shows them filled in for a new key.
- Methodology: `docs/adr/0025-paired-item-level-bootstrap-with-max-drop.md`; 41 ADRs in `docs/adr/`; `docs/eng-log.md`.
- **Absent, never fabricate:** customer logos, testimonials, case studies, user or star counts, benchmarks, press.

## Product Principles

1. **Show the mechanism.** Every claim sits next to the real config, command, or output that backs it; nothing is mocked.
2. **The trajectory is the unit of truth.** What the agent did matters as much as what it said.
3. **Don't cry wolf.** A regression signal has to be statistically defensible, and uncertainty (underpowered scorers) is shown, not hidden.
4. **Yours by default.** Open source, OpenTelemetry in, the user's machines, the user's code run only on the user's side.
5. **Use the domain's words.** Glossary terms everywhere: UI, docs, and marketing say the same thing.

## Accessibility & Inclusion

WCAG 2.2 AA across the web app and the marketing surface: contrast on text and status colors, full keyboard access, visible focus, screen-reader labels on charts and span trees, and `prefers-reduced-motion` respected. Pass and fail are never shown by color alone.
