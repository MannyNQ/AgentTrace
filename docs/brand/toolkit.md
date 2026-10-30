# Trayce brand toolkit

The source of truth for how Trayce looks, sounds, and argues. Use it for the landing page, the README, the GitHub description, launch posts, and any UI copy. Product vocabulary is in [`GLOSSARY.md`](../../GLOSSARY.md); where they overlap, the glossary wins.

How to use it with Impeccable is in [section 12](#12-using-this-with-impeccable).

---

## 1. Brand core

**What it is.** Open-source tracing and evals for LLM agents. It records every LLM call and tool call as a span tree, turns a failed trace into a test case, and grades the agent's trajectory in CI so a prompt or model change can't quietly break it.

**Category.** Agent observability and evals. Don't invent a new category name: the buyer searches for "LLM tracing" and "agent evals," and a coined term costs them a translation step.

**The problem we name.** Agent changes ship on vibe-checks. Someone tweaks a prompt, tries three questions, it looks fine, and the agent now skips the refund-policy lookup on a fifth of tickets. Nobody finds out until a customer does. The final answer often still reads well: the break is in *what the agent did*, not what it said.

**The promise.** Every failure you've seen becomes a test you keep. Every change is checked against all of them before it merges.

**Positioning statement** (internal, not copy):

> For engineers who ship LLM agents to production, Trayce is the tracing and eval tool that turns production failures into trajectory tests and blocks the pull request that would bring them back. Unlike tools that score only the final answer, or flag any dip in an average, Trayce grades which tools the agent called and in what order, and fails CI only on a regression the statistics can stand behind. It's MIT-licensed, speaks OpenTelemetry, and runs on your own machines.

**The one thing a neighbor can't copy truthfully:** *failure → test → gate* as one loop, with the trajectory as the thing under test. Tracing alone is table stakes. Evals alone are table stakes. The loop is the brand.

### Brand pillars

| Pillar | Claim | Why it's true (proof in the repo) |
|---|---|---|
| **See it** | Every LLM call and tool call, on a timeline, as a span tree. | Python SDK, OTLP ingest at `/otlp/v1/traces`, GenAI conventions and OpenInference (ADR-0035, ADR-0039). |
| **Keep it** | One click turns a failed trace into a dataset item. The trace is pinned so it never expires. | "Add to dataset", pinning (ADR-0021), versioned datasets (ADR-0005, ADR-0020). |
| **Gate it** | `trayce eval --fail-on-regression` fails the PR on a real regression and posts the comparison. | Paired item-level bootstrap with per-scorer `max_drop` (ADR-0025), merge-base baseline (ADR-0026), `action.yml`. |
| **Prove the fix** | `trayce replay` re-runs the failed trace with your current code and shows where the runs first differ. | ADR-0028, ADR-0040. |

Foundation, under all four: **yours.** MIT license, Docker Compose, OpenTelemetry in, no code runs on our side (ADR-0006: the eval runner runs on the client).

---

## 2. Audience

### Primary: the agent engineer

A senior backend or ML engineer who owns an agent in production: a support bot, a coding agent, an internal ops agent. Writes Python. Has OpenTelemetry somewhere in the stack already, or knows they should.

- **Moment we meet them:** right after a regression they shipped. They're between incidents, with an afternoon to evaluate tools, and a teammate just asked "how would we have caught that?"
- **What they want:** to stop finding out from customers. To change a prompt without fear.
- **What they distrust:** demos that only show a pretty dashboard; "AI-powered" anything; eval scores that move on noise; tools that need their data on someone else's servers before they've decided to trust them.
- **How they evaluate:** they read the config and the CLI output before the copy. They run the quickstart. They check the license and the GitHub activity.
- **What wins them:** a real trace, a real `eval.yaml`, a real failing PR comment, and a quickstart that works in five minutes on their laptop.

### Secondary: the engineering lead or platform owner

Responsible for several agents and the people changing them. Cares about the gate in CI, who can see what (owners, members, API key scopes), and whether it can run inside their network. Reads the "Running it for a team" section of the README.

### Who we're not talking to (yet)

Non-technical buyers, procurement, and people who want a no-code prompt playground. Writing for them would soften every sentence the primary audience reads.

### Objections, and the honest answer

| They think | We answer with |
|---|---|
| "I already have tracing." | Keep it: point your OTLP exporter at us. Tracing is the input; the loop from failure to CI gate is the product. |
| "My evals are flaky." | That's the problem ADR-0025 exists for: samples of one item are resampled together, a drop has to clear `max_drop` and a 95% interval, and underpowered scorers are flagged instead of silently passing. |
| "The answer is right, why grade the path?" | Because the answer can be right by luck and the path tells you it won't be next time: the agent skipped the lookup, called the tool twice, or took eleven steps. |
| "Is this going to be abandoned?" | Show the engineering log, ADRs, and commit history. Don't promise a roadmap. |
| "What does it cost?" | Undecided. Say what's true: it's MIT-licensed and you run it yourself. Don't speculate. |

---

## 3. Competitive frame

The buyer is comparing us with Langfuse, LangSmith, Braintrust, and Arize Phoenix. The rules:

1. **Never name a competitor in public copy**, and never characterize what they can or can't do. We haven't verified their feature sets, and they change monthly. A wrong claim about a competitor costs more trust than any right claim earns.
2. **Win on mechanism, not on adjectives.** Instead of "more accurate evals," show the bootstrap and the `max_drop` line in the config.
3. **Accept overlap gracefully.** Everyone has traces. Our page spends less time on tracing than a competitor's would, and more on what happens *after* the trace.

"Sentry plus pytest for AI agents" is a good **explainer** (README, conversation, a sub-line) because engineers decode it instantly. Don't make it the headline or the logo lockup: it borrows two other brands' names as our identity.

---

## 4. Messaging

### Headline candidates

Ranked. The first is the recommendation.

1. **Your agent's failures, as tests it has to pass.**
2. Every bad trace becomes a test. Every PR runs them.
3. Catch the prompt change that breaks your agent, before it merges.
4. Grade what your agent did, not just what it said.
5. Stop finding agent regressions in production.

### Sub-line (pairs with headline 1)

> Trayce records every LLM and tool call your agent makes, turns a failed run into a dataset item in one click, and fails the pull request when a change makes the agent worse. Open source, OpenTelemetry-native, runs on your machines.

### Message by pillar

- **See it:** "Every LLM call and tool call, with inputs, outputs, tokens, cost, and latency, in one span tree."
- **Keep it:** "Found a bad run? Add it to a dataset. Write what it should have done. It's a test now, and the trace stays."
- **Gate it:** "`trayce eval` runs your agent over every item, several times each, and compares the result with the run at your merge-base. It fails the build only when the drop is real."
- **Prove the fix:** "Re-run the trace that failed with the code on your branch, and see the step where it now goes a different way."
- **Yours:** "MIT-licensed. Docker Compose up. Point your existing OpenTelemetry exporter at it."

### Lines worth reusing

- "Grade the trajectory, not the vibe."
- "The answer can be right by luck. The path tells you whether it will be next time."
- "A test for every incident you've already had."
- "A regression gate that doesn't cry wolf."
- "Your traces, your machines."

### Calls to action

| Context | Primary | Secondary |
|---|---|---|
| Landing hero | **Run it locally** (jumps to the quickstart) | **Read the docs** or **View on GitHub** |
| After the CI section | **Add the GitHub Action** | |
| Footer | **View on GitHub** | |

Never "Get started for free", "Sign up", "Book a demo", or "Start your trial": they imply a hosted service, a price, or a sales process, none of which exist.

---

## 5. Voice and tone

**Personality in four words:** precise, blunt, calm, generous.

- **Precise:** say the mechanism. "Fails the PR when the pass rate drops more than `max_drop` and the 95% interval is below zero," not "smart regression detection."
- **Blunt:** say the bad thing plainly. "Your agent will regress. The question is whether CI notices first."
- **Calm:** no urgency theater, no exclamation marks, no "revolutionary." An on-call engineer reads us; we're the quiet person in the incident channel who has the graph.
- **Generous:** show the real config, the real output, the whole quickstart. Hand over the tool; don't tease it.

### Rules

- Sentence case for every heading and button.
- Second person ("your agent"), active voice, present tense.
- Short sentences. One idea each. If a sentence needs a semicolon, it's two sentences.
- Numbers as numerals, units always: "8 KB", "30 days", "10,000 rounds".
- Code, commands, flags, file names, and config keys in `monospace`, always copyable, always real.
- No emoji. No exclamation marks. No rhetorical questions in headlines.
- Name a feature by what it does before what it's called: "Re-run a failed trace with your current code (`trayce replay`)."

### Rewrites

| Don't | Do |
|---|---|
| Supercharge your AI agents with next-gen observability! | See every LLM and tool call your agent makes. |
| AI-powered evals that just work. | An LLM judge for outputs, and exact checks for which tools were called, in order. |
| Seamlessly integrates with your stack. | Point any OTLP exporter at `/otlp/v1/traces`. |
| Enterprise-grade security. | Owners manage members and API keys. `ingest` keys can only send spans. |
| Ship with confidence. | Merge when the eval passes. |

### Lexicon

Use the glossary's terms exactly: **trace, span, LLM span, tool span, trajectory, dataset, dataset version, item, experiment, scorer, replay, project, API key.**

| Avoid | Because |
|---|---|
| generation | Glossary term is LLM span. |
| test suite, test set | It's a dataset, with versions. |
| AI-powered, intelligent, smart | Says nothing; reads as hype. |
| seamless, effortless, magic | Engineers hear "untested." |
| platform (in headlines) | Vague; say what it does. |
| enterprise, cloud, SaaS, free tier, pricing | Not decided or not true. |
| guarantee, never, 100% | The gate is statistical; we don't overclaim. |
| hallucination detection | Not a feature we ship. |

---

## 6. Claims guardrails

Hard rules. Any page, post, or README edit that breaks one is wrong, however good it reads.

**True, and fine to say:**

- MIT-licensed, open source ([`LICENSE`](../../LICENSE)).
- Runs on your own infrastructure with Docker Compose.
- Python SDK. Any OpenTelemetry exporter over OTLP/HTTP also works.
- Reads OpenTelemetry GenAI conventions and OpenInference; works with agents instrumented through Pydantic AI, the Vercel AI SDK, and OpenInference instrumentors.
- One-click "Add to dataset"; versioned datasets; linked traces are pinned past retention.
- `trayce eval` grades outputs and trajectories; scorers include `contains`, `trajectory` (`in_order`, `max_steps`, and others), and `llm_judge` (an Anthropic model, binary verdicts).
- `--fail-on-regression` and the GitHub Action fail a PR on a statistically real regression and post the comparison as a comment.
- `trayce replay` re-runs a trace with current code and shows where runs first differ.
- Project overview: volume, error rates, latency percentiles, spend by model, and tools, each chart linking to its traces.

**Never say or imply:**

- A hosted, cloud, or SaaS version; a free tier; any price.
- Customer names, logos, testimonials, user counts, GitHub-star counts you haven't checked that day, or "trusted by."
- Benchmarks, throughput, scale, or latency numbers we haven't measured and published with a method.
- Compliance (SOC 2, HIPAA, GDPR) or "enterprise-ready."
- SDKs in languages other than Python.
- A bare `pip install trayce` as the way to run the server. Ubuntu's, Debian's, and Homebrew's Python refuse it; write `pipx install trayce` (or `uv tool install trayce`) then `trayce up`. `pip install trayce` is right only for adding the SDK to an agent's environment.
- Anything about a competitor.

---

## 7. Proof architecture

We have no social proof, so the product is the proof. Every claim on the landing page sits next to the artifact that backs it.

| Proof | Where it comes from | Use it for |
|---|---|---|
| A real span tree | `uv run demo-agent "Where is order 1042?"`, then the trace page | Hero visual; "See it" |
| A real `eval.yaml` | [`examples/demo-agent/eval.yaml`](../../examples/demo-agent/eval.yaml) | "Gate it": the config *is* the pitch |
| Real `trayce eval` output | Run it against a published `support` dataset; capture the terminal | "Gate it" |
| The PR comparison comment | A PR in this repo run through `action.yml` | The closer: the failing check |
| A replay with a divergence | `trayce replay <trace_id>` on a deliberately broken prompt; the replay page | "Prove the fix" |
| OTLP env vars | README, "OpenTelemetry" section | "Yours": three lines, done |
| The methodology | [ADR-0025](../adr/0025-paired-item-level-bootstrap-with-max-drop.md) | A "how the gate decides" disclosure, linked, not summarized into marketing |
| The ADRs and eng log | `docs/adr/`, `docs/eng-log.md` | Seriousness signal in the footer: "41 architecture decisions, written down" |

Rules: capture every screenshot and output from a real run, never hand-edit numbers, and keep the command that produced it next to it so a reader can reproduce it. If a visual is a mock, it doesn't go on the page.

---

## 8. Visual identity direction

This is direction, not a finished system. Impeccable builds the visual world from it (section 12).

### Concept: the divergence

The product's most honest picture is **two runs of the same agent that share a path and then split**: the baseline calls `lookup_order` then `check_policy`; the change calls `lookup_order` and stops. Replay shows it, the regression gate catches it, the trajectory scorer grades it. Make that fork the brand's signature:

- In the **logo mark**: two parallel strokes that run together and then part, one ending in a filled node. Simple enough for a 16 px favicon.
- In **illustration and diagrams**: trajectories drawn as horizontal lines of tool-call nodes, in monospace labels, with the divergence point marked.
- In **layout**: let one horizontal rule run through a section and branch, rather than boxing content in cards.

What the brand is not: glowing neural nets, robot heads, sparkles, gradient blobs, abstract "AI" particles, or a dashboard collage.

### Material: the test report

The feel is a well-made engineering artifact: a test report, a terminal, a lab notebook. Dense, legible, quiet, with one mark of emphasis per screen. Generous with real content, stingy with decoration.

### Color

Continuity with the app matters: someone who clicks "Run it locally" should land in a product that looks like the page they came from. The app uses zinc neutrals with green for pass and red for fail ([`web/src/app/globals.css`](../../web/src/app/globals.css)).

- **Neutrals:** the app's zinc scale, light and dark. Impeccable may tint them slightly; keep them close enough that app and site read as one family.
- **Pass and fail:** the app's `--good` and `--bad`, reserved for meaning. Never decorative.
- **One brand accent: highlighter yellow.** The color of marking the line in a log that explains the incident. Used as a *background* behind text or a span (like `<mark>`), never as text color, so contrast stays on the ink. One use per screen: the divergence point, the regressed scorer, the line in the config that matters.
- Charts keep the app's validated series palette. If highlighter yellow shares a screen with a chart, leave `--series-4` (amber) out of it.
- No purple, no blue-to-purple gradients, no neon.

### Typography

- **Monospace carries the data.** Code, commands, span names, tool names, scores, IDs, numbers in tables. The app uses Geist Mono; keep it for continuity.
- **One sans for prose and headlines** with more character than the app's Geist Sans: a grotesque with some engineering feel, not a geometric startup face and not Inter. Impeccable's `typeset` picks the face from this brief.
- Big type is earned by short words. A headline is at most two lines at desktop width.

### Imagery and motion

- Imagery is product screenshots and terminal captures from real runs, cropped tight to the part that proves the point, at 1:1 pixel density, with annotations drawn in the brand accent.
- Motion explains, never decorates: a trajectory drawing node by node, two runs splitting at the divergence. Respect `prefers-reduced-motion`. No bounce or elastic easing.

### Logo

- **Wordmark:** "Trayce", one word, capital T, the rest lowercase, set in the brand sans. Never "TRAYCE" in prose, never "Trace", and never "Traycer" (a different product).
- **Mark:** the divergence glyph described above.
- **CLI and package names:** `trayce`, always lowercase in monospace.

### Naming note

The product was called AgentTrace until October 2026. That name was already taken several times over in the same category: `agenttrace` on PyPI (TensorStax), `agenttrace-sdk` (agenttrace.io, a live agent observability product), `agenttrace-py` (agenttrace.com, deterministic replay), and `agenttrace-ai`. We renamed to Trayce so search results, package installs, and the repo point at one project. ADR-0042 records the decision.

Two nearby names to stay distinct from:

- **Trayce API client** at trayce.dev, an HTTP and network traffic tool for Docker. Different product, same spelling. Don't link to trayce.dev or imply any connection.
- **Traycer**, an AI coding tool. Never add the trailing "r", and don't borrow its look.

Old captures in `site/proof/v1/` predate the rename and still say AgentTrace; that's expected.

---

## 9. Copy bank

### Short descriptions

- **GitHub "About" (≤ 120 chars):** Open-source tracing and evals for LLM agents. Turn failed traces into tests and gate PRs on trajectory regressions.
- **One sentence:** Trayce traces every LLM and tool call your agent makes, turns failures into tests, and fails the pull request that brings them back.
- **One paragraph:** Agents regress quietly: a prompt tweak, a new model, and the agent skips a step it used to take. Trayce records every LLM and tool call as a trace, turns a bad run into a dataset item in one click, and grades the agent's trajectory (which tools it called, in what order) on every pull request. The CI gate fails only when the drop is statistically real. It's MIT-licensed, reads OpenTelemetry, and runs on your own machines.

### Landing page section headers

- What your agent actually did
- Every failure, kept as a test
- The gate on every pull request
- How the gate decides
- Check the fix against the run that failed
- Bring your OpenTelemetry
- Run it in five minutes

### Microcopy

- Code-block copy button: "Copy" → "Copied"
- Empty state, no traces yet: "No traces yet. Run your agent with the SDK or point an OTLP exporter here."
- Footer: "MIT-licensed. Built in the open. Every decision is in `docs/adr/`."

### Launch post opener

> I changed one line of a support agent's prompt. It still answered every question I tried. It also stopped checking the refund policy before promising refunds. I built Trayce so the next change like that fails CI instead of reaching a customer.

(Only use first-person stories that actually happened. Edit this to the real one.)

---

## 10. Landing page narrative

The order the page argues in, for the prompt:

1. **Hero:** headline, sub-line, "Run it locally", and a real span tree with one tool span highlighted.
2. **The problem:** two trajectories side by side, the same question, a one-line prompt change, the divergence marked. Both final answers look fine.
3. **See it:** the trace view; OTel and SDK in two short code blocks.
4. **Keep it:** "Add to dataset", the item with its expected trajectory.
5. **Gate it:** the real `eval.yaml`, then the real failing PR comment. This is the peak of the page.
6. **How the gate decides:** a short, plain explanation of the paired bootstrap and `max_drop`, linking to ADR-0025. For the skeptic; collapsible is fine.
7. **Prove the fix:** replay beside the original, first difference marked.
8. **Yours:** MIT, Docker Compose, OTLP env vars, "no code runs on our side."
9. **Quickstart:** the four commands from the README, copyable, ending at a trace on screen.
10. **Footer:** GitHub, docs, ADRs, license.

No logo strip, testimonial carousel, pricing table, or "trusted by" section, not even as placeholders.

---

## 11. Open decisions

Record these as undecided everywhere; don't fill them in.

- **Package name on PyPI.** Settled: the distribution, import, and CLI are all `trayce`. Published on PyPI from 0.1.0 (2026-10-30).
- **Hosted offering and pricing.** Not decided.
- **Public docs site.** The README and `docs/` are the docs today; CTAs link there.
- **Logo files.** The mark is a direction (section 8); no final asset exists.
- **Domain.** Still open; don't put a URL in copy. trayce.dev belongs to another product (the Trayce API client), and trayce.com is parked and listed as possibly for sale.

---

## 12. Using this with Impeccable

1. **`/impeccable init`.** It writes `PRODUCT.md` from durable product facts. Feed it sections 1, 2, 6, 7, and 11 of this toolkit: users, purpose, positioning, constraints, evidence on hand, and the open decisions. Tell it the voice rules in section 5 and the logo and naming rules in section 8 are binding brand commitments.
2. **New work: the landing page.** Give it the prompt below. New-work builds the visual world (`DESIGN.md`) from section 8's direction, keeping continuity with `web/src/app/globals.css`.
3. **After the build:** `/impeccable critique landing` against this toolkit, `/impeccable clarify` against section 5, and `/impeccable audit` for accessibility and responsiveness.

### The landing page prompt

> Make the landing page for **Trayce**, following `docs/brand/toolkit.md` as the brand authority: its voice rules, lexicon, claims guardrails, proof architecture, visual direction, and the narrative in section 10.
>
> Trayce is open-source tracing and evals for LLM agents. It records every LLM and tool call as a span tree, turns a failed trace into a dataset item in one click, and grades the agent's trajectory (which tools it called, in what order) in CI. The GitHub Action fails a pull request only on a statistically real regression against the merge-base baseline: a paired item-level bootstrap with a per-scorer `max_drop` (ADR-0025).
>
> **Audience:** senior engineers who ship LLM agents to production, between incidents, comparing us with other agent observability and eval tools. They read config and CLI output before copy.
>
> **Brand:** precise, blunt, calm, generous. No hype words; use the glossary's terms exactly.
>
> **Primary action:** run the stack locally (clone, `docker compose up -d --wait`) and send traces from an agent, through the Python SDK or an existing OpenTelemetry exporter pointed at `/otlp/v1/traces`. **Secondary:** add the GitHub Action.
>
> **Say:** MIT-licensed, open source, runs on your own infrastructure with Docker Compose, OpenTelemetry GenAI and OpenInference support, `llm_judge` with an Anthropic model, replay with first-difference view.
>
> **Don't say:** hosted, cloud, SaaS, free tier, any price, compliance, performance numbers, non-Python SDKs, `pip install trayce`, or anything about a competitor.
>
> **Show:** a real span tree from the demo agent ("Where is order 1042?"), the real `examples/demo-agent/eval.yaml`, real `trayce eval` output, the PR comparison comment, a replay's divergence, and the OTLP env vars. There are no customer logos, testimonials, or user counts. Don't invent them or leave placeholders.
>
> **Where:** a route in the Next.js app in `web/`, served at `/` to signed-out visitors; signed-in users still go to their projects.
