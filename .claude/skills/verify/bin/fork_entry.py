"""The demo agent as `agenttrace replay`'s entry for the fork recipe (features/fork.md).

FORK_PROMPT=broken swaps in a system prompt that skips the refund policy, to record the failure;
FORK_PROMPT=fixed adds an explicit check, as a fix would. FORK_TOOL_LOG names a file each tool
appends its name to when it really runs, so a tool answered from the recording leaves no line.
Verification scaffolding only: the demo agent itself is unchanged.
"""

import functools
import os
from pathlib import Path

from demo_agent import agent, tools
from demo_agent.evals import eval_entry  # loads .env and instruments Anthropic

POLICY = "Refunds follow the refund policy exactly."
PROMPTS = {
    "broken": agent.SYSTEM.replace(
        POLICY, "Refund whatever the customer asks for right away; don't check the refund policy."
    ),
    "fixed": agent.SYSTEM.replace(
        POLICY,
        POLICY + " Before calling issue_refund, look the order up and check its delivery date "
        "against the 30-day window; never call issue_refund for an order outside it.",
    ),
}
if prompt := os.environ.get("FORK_PROMPT"):
    agent.SYSTEM = PROMPTS[prompt]  # run() reads it at each call


def _logged(name, fn):
    @functools.wraps(fn)
    def run(**args):
        with Path(os.environ["FORK_TOOL_LOG"]).open("a") as f:
            f.write(f"{name}\n")
        return fn(**args)

    return run


if os.environ.get("FORK_TOOL_LOG"):
    for name, (fn, desc, props) in list(tools.TOOLS.items()):
        tools.TOOLS[name] = (_logged(name, fn), desc, props)

entry = eval_entry

if __name__ == "__main__":  # record one production run of the question; print its trace ID
    import sys

    from agenttrace import add_span_processor
    from agenttrace.exporter import flush

    roots = []
    add_span_processor(lambda s: roots.append(s.trace_id) if s.is_root else None)
    entry({"question": sys.argv[1]})
    flush(timeout_s=5)
    print(roots[-1])
