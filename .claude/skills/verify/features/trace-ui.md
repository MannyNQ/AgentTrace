# Trace UI

The web app lists the project's traces, newest first, with root span name, status (`ok`, `error`, or `running` until complete), latency, span count, cost, and tags. Opening a trace shows its header, an indented span tree with kind badges, durations, error marks and a timeline bar, and a detail panel for the selected span with its input, output, LLM fields, and attributes. A large payload shows a preview and a link to the full payload from S3.

## Sub-features

- `ui-trace-list` lists traces at `/traces`, newest first, 50 per page with an "Older →" link.
- `ui-trace-status` shows `ok`, `error` for a root that failed or never closed, and `running` before completion.
- `ui-trace-cost` shows the summed cost, `≥ $x` when some LLM spans are unpriced, and `—` when none are priced.
- `ui-trace-tree` nests spans under their parents at `/traces/<trace_id>`, marks failed spans `✗`, and keeps a recovered tool error from failing the trace.
- `ui-span-detail` shows the selected span (`?span=<span_id>`): input and output, model, tokens and cost for LLM spans, and attributes.
- `ui-full-payload` serves an offloaded payload at `/p/<slug>/api/traces/<trace_id>/spans/<span_id>/<input|output>`, which ingest reads from S3 after checking the session and project.

## How to get to it (user POV)

- From the overview (where `/` lands), follow "Traces" in the nav, then click a trace name, then click a span in the tree. Eval runs and replays are hidden until "Show eval runs" or "Show replays".

## Driving it with agenttrace-verify

Preconditions:

- A healthy run, with traces sent by `H emit` (and, for the agent journey, `H agent` runs; see `demo-agent.md`).
- Ingest runs with `TRACE_QUIET_PERIOD_S=20` (the helper exports it to compose), so an unclosed trace turns `error` 20 s after its last span. Ingest decides trace status now, not the web app.

- **List.** Run `H traces`. `traces.summary` has one line per trace: `<trace_id> | <started> | <name> | <status> | <latency> | <spans> | <cost> | <tags>`. `verify.nested` is `ok` with 4 spans, cost `$0.0140` and tag `verify`; `verify.root-error` is `error`; `verify.unpriced` shows cost `—`.
- **Completion.** Right after `H emit`, `H traces '' traces-early` shows `(root span missing)` as `running`. 25 s later, `H traces '' traces-late` shows it as `error`.
- **Tree.** Run `H traces <nested trace_id> trace-nested`. The summary has `TRACE verify.nested status=ok`, then `[agent] verify.nested` with `[llm]`, `[tool] lookup_order ✗ ToolError` and `[retrieval]` indented one level below it.
- **Span detail and payload link.** Run `H traces <large_payload trace_id> trace-large`, then `grep -c 'Full payload' verify-artifacts/<run-id>/trace-large.out` is `1`. curl the link and check its byte length.
- **Visual.** In the browser pane, sign in, open `http://127.0.0.1:13000/p/default/traces`, take a screenshot, click a trace, click a span, and screenshot again. Copy the screenshots into the run dir.

## Gotchas

- The list and tree stream inside `<Suspense>`. Use `H traces` (full server response) for text proof; in a hidden browser pane, take a screenshot to force a frame (see `SKILL.md`).
- Status depends on the clock. `running` is only visible within the quiet period, so capture it right after the spans are sent.
- The trace list reads `traces` aggregate rows. A duplicate the worker missed (crash between insert and dedup mark, ADR-0014) would inflate span count there while the tree, which reads `spans FINAL`, shows one span.
