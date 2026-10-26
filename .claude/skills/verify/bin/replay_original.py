"""Record a 'production' trace of the scripted support agent (.github/eval-gate/gate_agent.py), for
the replay recipe: its root span input is what `agenttrace replay` re-runs. Set EVAL_GATE_BROKEN=1
to record the failure. Prints the trace ID.

    EVAL_GATE_BROKEN=1 AGENTTRACE_API_KEY=... AGENTTRACE_ENDPOINT=http://127.0.0.1:18000 \
        uv run python .claude/skills/verify/bin/replay_original.py "Refund order 1043?"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / ".github" / "eval-gate"))

import gate_agent  # noqa: E402

from agenttrace import span  # noqa: E402
from agenttrace.exporter import flush  # noqa: E402

question = {"question": sys.argv[1]}
with span("support_agent", "agent", input=question) as root:
    root.set_output(gate_agent.entry(question))
flush(timeout_s=5)
print(root.trace_id)
