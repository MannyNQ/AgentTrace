# Demo agent

The Keyly support agent answers customer questions in a terminal by calling five tools (`lookup_order`, `check_inventory`, `issue_refund`, `search_kb`, `escalate`) against fixed demo data. It shows each tool call with ✓ or ✗ and then a short answer. It's the app AgentTrace will trace from Phase 1, so its observable behavior must stay stable.

## Sub-features

- `agent-order-status` looks up an order and reports carrier and ETA.
- `agent-tool-error` recovers when a tool fails (an unknown order) and tells the user, without crashing.
- `agent-refund-allowed` refunds a delivered order inside the 30-day window.
- `agent-refund-refused` declines a refund outside the window and explains the policy.
- `agent-escalate` hands warranty claims to a human.
- `agent-traced` sends each run to AgentTrace: a `support_agent` agent span with an LLM span per model call and a tool span per tool call, priced and shown in the UI.

## How to get to it (user POV)

- Run `uv run demo-agent "<question>"` from the repo root, with `ANTHROPIC_API_KEY` set in `.env`.

## Driving it with agenttrace-verify

Preconditions:

- `.env` has a non-empty `ANTHROPIC_API_KEY`. Check without printing it: `grep -q '^ANTHROPIC_API_KEY=.\+' .env`. If it's missing, every step below is not verified, so report that and stop.
- A verify run is active (`H doctor`). `H agent` writes into its run dir and traces the run into the instance.
- Each step makes real Claude API calls.

- **Order status.** Run `H agent "Where is order 1042?"`, then rename the capture: `mv verify-artifacts/<run-id>/agent.out verify-artifacts/<run-id>/agent-order-status.out` (and the same for `.cmd` and `.err`). The `.cmd` shows `exit: 0`, the output has a `✓ lookup_order({'order_id': '1042'})` line, and the answer mentions `UPS` and October 28.
- **Tool error.** Run `H agent "Where is order 9999?"`, then rename the captures to `agent-tool-error.*`. Exit is 0, there's a `✗ lookup_order` line containing `No order with id '9999'`, and the answer says the order wasn't found.
- **Refund allowed.** Run `H agent "Please refund order 1043, both mice stopped working."`, then rename to `agent-refund-allowed.*`. There's a `✓ issue_refund` line with `"refunded_usd": 58.0`, and the answer confirms a $58 refund.
- **Refund refused.** Run `H agent "I want a refund for order 1044."`, then rename to `agent-refund-refused.*`. Either a `✗ issue_refund` line contains `outside the 30-day window`, or the agent checks the order and policy first and declines without calling `issue_refund`. Either way the answer declines and cites the 30-day policy, and no `✓ issue_refund` line appears.
- **Escalate.** Run `H agent "My headphones from order 1044 have a dead left ear, can I get it fixed under warranty?"`, then rename to `agent-escalate.*`. There's a `✓ escalate` line, and the answer gives the ticket `HUMAN-0001`.

- **Traced.** After the steps above, run `H traces '' traces-agent`. There's one `support_agent` line per run, each `ok` (the tool-error run too, since the agent recovers) with tag `demo-agent` and a cost in dollars. Then `H traces <trace_id of the tool-error run> trace-agent-tool-error`: `[agent] support_agent` with `[llm] anthropic.messages.create` and `[tool] lookup_order ✗ ToolError` beneath it. Check the LLM span's model and cost with `H ch agent-llm-cost "SELECT model, input_tokens, output_tokens, cost_usd FROM spans WHERE kind='llm' ORDER BY start_time"`: every row has a non-null cost equal to `(input_tokens*4 + output_tokens*20)/1e6` for `claude-opus-5-5`.

## Gotchas

- Without a key, the CLI fails with `TypeError: Could not resolve authentication method` before any tool runs. That's a precondition failure, not an agent bug. Never substitute a fake client to make it pass.
- The refund window is measured from the real current date. Order 1043 was delivered 2026-10-10, so `agent-refund-allowed` only holds until 2026-11-09. After that it correctly refuses, so update the demo data in `examples/demo-agent/src/demo_agent/data.json` rather than the assertion.
- Order 1043 has two mice. Asking about "the mouse" makes the agent rightly confirm before refunding the whole $58 order, so the question names both.
- The agent is told today's date in its system prompt. Without it, the model judged the refund window itself and allowed a refund on 1044, 85 days after delivery.
- Model wording varies between runs. Assert on tool lines, numbers, IDs and ticket names, not phrasing.
- `H agent "<q>" <name>` names the capture directly; without a name it overwrites `agent.*` on every call.
