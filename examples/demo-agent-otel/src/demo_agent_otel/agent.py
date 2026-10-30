"""The demo agent's support tools on Pydantic AI, traced only with OpenTelemetry.

Pydantic AI's instrumentation records the run, each model request, and each tool call as spans in
OpenTelemetry's GenAI conventions; nothing here imports `trayce`. The model is a
`FunctionModel` running a fixed policy, so it needs no API key, no network, and answers the same
way every time, like the demo agent's mock tools.
"""

import re
from typing import Any

from pydantic_ai import Agent, ModelRetry
from pydantic_ai.capabilities.instrumentation import Instrumentation
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from demo_agent import tools

MODEL_NAME = "scripted-support"  # not in the price table, so its LLM spans have no cost
ORDER_ID = re.compile(r"\b(\d{4})\b")
REFUND = re.compile(r"\b(refund|return)\b", re.IGNORECASE)


def _tool_results(messages: list[ModelMessage]) -> dict[str, tuple[Any, bool]]:
    """Each tool's latest result so far, and whether it failed."""
    results: dict[str, tuple[Any, bool]] = {}
    for message in messages:
        for part in message.parts:
            if isinstance(part, RetryPromptPart) and part.tool_name:
                results[part.tool_name] = (part.content, True)
            elif isinstance(part, ToolReturnPart):
                failed = getattr(part, "outcome", "success") != "success"
                results[part.tool_name] = (part.content, failed)
    return results


def _question(messages: list[ModelMessage]) -> str:
    for message in messages:
        for part in message.parts:
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                return part.content
    return ""


def _call(tool: str, **args: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool, args)])


def _say(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(text)])


def _error(content: Any) -> str:
    return content if isinstance(content, str) else str(content)


def policy(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Look the order up; refund it when asked; otherwise search help articles, then escalate."""
    question, done = _question(messages), _tool_results(messages)
    if match := ORDER_ID.search(question):
        order_id = match.group(1)
        if "lookup_order" not in done:
            return _call("lookup_order", order_id=order_id)
        order, failed = done["lookup_order"]
        if failed:
            return _say(_error(order))
        if REFUND.search(question):
            if "issue_refund" not in done:
                return _call("issue_refund", order_id=order_id, reason=question)
            refund, failed = done["issue_refund"]
            if failed:
                return _say(f"I can't refund order {order_id}: {_error(refund)}")
            return _say(f"Refunded ${refund['refunded_usd']:.2f} for order {order_id}.")
        answer = f"Order {order_id} is {order['status']}"
        if order.get("carrier"):
            answer += f", shipping with {order['carrier']}"
        if order.get("eta"):
            answer += f", expected {order['eta']}"
        return _say(answer + ".")
    if "search_kb" not in done:
        return _call("search_kb", query=question)
    articles, _ = done["search_kb"]
    if articles:
        return _say(f"{articles[0]['title']}: {articles[0]['body']}")
    if "escalate" not in done:
        return _call("escalate", reason=question)
    ticket, _ = done["escalate"]
    return _say(f"A person will follow up on ticket {ticket['ticket']}.")


agent = Agent(
    FunctionModel(policy, model_name=MODEL_NAME),
    name="support",
    instructions="You answer questions about Keyly orders, refunds, and products.",
    capabilities=[Instrumentation()],
)


def _run(fn: Any, **args: Any) -> Any:
    """A tool error goes back to the model to recover from, as in the demo agent."""
    try:
        return fn(**args)
    except tools.ToolError as e:
        raise ModelRetry(str(e)) from e


@agent.tool_plain
def lookup_order(order_id: str) -> dict:
    """Look up an order's status, items, carrier, and dates."""
    return _run(tools.lookup_order, order_id=order_id)


@agent.tool_plain
def check_inventory(sku: str) -> dict:
    """Check stock for a product SKU."""
    return _run(tools.check_inventory, sku=sku)


@agent.tool_plain
def issue_refund(order_id: str, reason: str) -> dict:
    """Refund a whole delivered order, within 30 days of delivery."""
    return _run(tools.issue_refund, order_id=order_id, reason=reason)


@agent.tool_plain
def search_kb(query: str) -> list[dict]:
    """Search help articles on policies, shipping, and warranty."""
    return _run(tools.search_kb, query=query)


@agent.tool_plain
def escalate(reason: str) -> dict:
    """Hand the conversation to a human agent."""
    return _run(tools.escalate, reason=reason)


def run(question: str) -> str:
    return agent.run_sync(question).output
