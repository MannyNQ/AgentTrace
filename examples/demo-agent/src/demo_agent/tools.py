"""Mock support tools backed by data.json. Deterministic, so evals over this agent repeat."""

import json
from datetime import date
from importlib.resources import files
from typing import Any

from anthropic.types.beta import BetaToolParam

DATA: dict[str, Any] = json.loads(files("demo_agent").joinpath("data.json").read_text())
REFUND_WINDOW_DAYS = 30


class ToolError(Exception):
    """A tool failed in a way the model should see and recover from."""


def lookup_order(order_id: str) -> dict:
    order = DATA["orders"].get(order_id)
    if order is None:
        raise ToolError(f"No order with id {order_id!r}.")
    return {"order_id": order_id, **order}


def check_inventory(sku: str) -> dict:
    item = DATA["inventory"].get(sku)
    if item is None:
        raise ToolError(f"No product with SKU {sku!r}.")
    return {"sku": sku, **item}


def issue_refund(order_id: str, reason: str, today: date | None = None) -> dict:
    order = lookup_order(order_id)
    if order["status"] != "delivered":
        raise ToolError(
            f"Order {order_id} is {order['status']}, not delivered; it can't be refunded."
        )
    age = ((today or date.today()) - date.fromisoformat(order["delivered_on"])).days
    if age > REFUND_WINDOW_DAYS:
        raise ToolError(
            f"Order {order_id} was delivered {age} days ago, outside the 30-day window."
        )
    return {"order_id": order_id, "refunded_usd": order["total_usd"], "reason": reason}


def search_kb(query: str) -> list[dict]:
    words = {w for w in query.lower().split() if len(w) > 2}
    scored = [(sum(w in f"{a['title']} {a['body']}".lower() for w in words), a) for a in DATA["kb"]]
    return [a for score, a in sorted(scored, key=lambda s: -s[0]) if score > 0][:2]


def escalate(reason: str) -> dict:
    return {"escalated": True, "ticket": "HUMAN-0001", "reason": reason}


def _schema(props: dict[str, str]) -> dict:
    return {
        "type": "object",
        "properties": {name: {"type": "string", "description": d} for name, d in props.items()},
        "required": list(props),
        "additionalProperties": False,
    }


TOOLS = {
    "lookup_order": (
        lookup_order,
        "Look up an order's status, items, carrier, and dates.",
        {"order_id": "The order number, e.g. 1042"},
    ),
    "check_inventory": (
        check_inventory,
        "Check stock for a product SKU.",
        {"sku": "Product SKU, e.g. KB-75"},
    ),
    "issue_refund": (
        issue_refund,
        "Refund a whole delivered order. Checks the 30-day window itself and refuses "
        "orders outside it.",
        {"order_id": "The order number", "reason": "Why the customer wants a refund"},
    ),
    "search_kb": (
        search_kb,
        "Search help articles on policies, shipping, and warranty.",
        {"query": "Search words"},
    ),
    "escalate": (
        escalate,
        "Hand the conversation to a human agent.",
        {"reason": "Why a human is needed"},
    ),
}

TOOL_DEFINITIONS: list[BetaToolParam] = [
    {"name": name, "description": desc, "input_schema": _schema(props), "strict": True}
    for name, (_, desc, props) in TOOLS.items()
]


def run_tool(name: str, args: dict) -> tuple[str, bool]:
    """Run a tool by name. Returns (JSON result or error message, is_error)."""
    if name not in TOOLS:
        return f"Unknown tool {name!r}.", True
    try:
        return json.dumps(TOOLS[name][0](**args)), False
    except ToolError as e:
        return str(e), True
