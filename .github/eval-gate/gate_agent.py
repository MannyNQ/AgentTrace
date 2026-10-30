"""A scripted support agent for the eval-gate workflow, which tests action.yml end to end without
a model key. It calls the demo agent's tools as tool spans and answers from a table, so its scores
are the same on every run. With EVAL_GATE_BROKEN=1 it answers without calling any tool, which
regresses both scorers."""

import os
import re

from trayce import span

ANSWERS = {
    "1042": (["lookup_order"], "Order 1042 shipped with UPS."),
    "1043": (["lookup_order", "issue_refund"], "I've refunded order 1043."),
    "1044": (["lookup_order"], "Refunds are possible within 30 days."),
    "1045": (["lookup_order"], "Order 1045 is on its way."),
}


def entry(input: dict) -> str:
    if os.environ.get("EVAL_GATE_BROKEN") == "1":
        return "I don't know."
    order = re.search(r"\d{4}", input["question"])
    tools, answer = ANSWERS.get(order.group(0) if order else "", ([], "I don't know."))
    for tool in tools:
        with span(tool, "tool", input={"order_id": order.group(0) if order else None}):
            pass
    return answer
