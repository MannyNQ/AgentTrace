"""The entry point `agenttrace eval` and `agenttrace replay` call (eval.yaml). The runner installs
the OTel bridge, so the agent's OpenTelemetry tool spans are the sample's trajectory (ADR-0036)."""

from demo_agent_otel.agent import run


def eval_entry(input: dict) -> str:
    return run(input["question"])
