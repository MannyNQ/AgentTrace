"""A customer-support agent on the Claude API, written as an explicit loop.

The loop is hand-written (not the SDK's tool runner) so each model call and each tool call is a
visible step: `run` is the agent span, `instrument_anthropic()` (called by the CLI) records each
model call as an LLM span, and each tool call is a tool span.
"""

import os
from dataclasses import dataclass, field
from datetime import date

import anthropic

from agenttrace import trace
from demo_agent.tools import TOOL_DEFINITIONS, run_tool

MODEL = os.environ.get("DEMO_AGENT_MODEL", "claude-opus-5-5")
MAX_TURNS = 8
SYSTEM = """You are the support agent for Keyly, a small online keyboard and accessories store.
Use the tools to look things up; never guess order details, stock, or policy.
Refunds follow the refund policy exactly. Escalate warranty claims and anything you can't resolve.
Answer in a few plain sentences."""


@dataclass
class ToolCall:
    name: str
    args: dict
    result: str
    is_error: bool


@dataclass
class AgentResult:
    answer: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None


class AgentError(Exception):
    pass


@trace(name="support_agent", kind="agent", tags=["demo-agent"])
def run(question: str, client: anthropic.Anthropic | None = None) -> AgentResult:
    client = client or anthropic.Anthropic()
    messages: list = [{"role": "user", "content": question}]
    calls: list[ToolCall] = []

    for _ in range(MAX_TURNS):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            # Without the date the model judges the refund window itself, and gets it wrong.
            system=f"{SYSTEM}\nToday is {date.today().isoformat()}.",
            tools=TOOL_DEFINITIONS,
            output_config={"effort": "low"},  # short support chats; Opus 5.5 defaults to medium
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # rerun on a fallback model if a safety classifier declines
            messages=messages,
        )
        # Append the full content (thinking blocks included) so history stays append-only.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            raise AgentError(f"model declined: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise AgentError("hit max_tokens before finishing")
        if response.stop_reason != "tool_use":
            answer = "".join(b.text for b in response.content if b.type == "text").strip()
            return AgentResult(answer=answer, tool_calls=calls, stop_reason=response.stop_reason)

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            # The agent recovers from tool errors: run_tool returns them for the model to see.
            output, is_error = run_tool(block.name, block.input)
            calls.append(ToolCall(block.name, block.input, output, is_error))
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                    "is_error": is_error,
                }
            )
        messages.append({"role": "user", "content": results})

    raise AgentError(f"no answer after {MAX_TURNS} turns")
