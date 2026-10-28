Captured 2026-10-27 from a local verify stack (git 27a9da5).

- demo-agent-1042.txt and trace-07a86a29….jsonl: uv run demo-agent "Where is order 1042?"
- eval-baseline.txt: agenttrace eval -c examples/demo-agent/eval.yaml on the merge-base, as main
- eval-pr-quiet.txt: the same with a softer one-line prompt edit; the agent kept looking orders up, and the gate stayed quiet
- eval-pr.txt, pr-comment.md: the same with prompt-change.diff applied, --base main --fail-on-regression --report-md
- trace-57d309f7….jsonl (main) and trace-1dc1fb97….jsonl (PR): sample 0 of item 56518cd4, "Can I get a refund on order 1044?"

Nothing here is edited. Links in pr-comment.md point at the local stack that produced it.
