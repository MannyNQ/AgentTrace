"""Create and publish the demo agent's `support` dataset in the verify instance, for
`trayce eval -c examples/demo-agent/eval.yaml`. Stdlib only. One item fails on purpose: its
expected trajectory escalates a plain status question, which the agent shouldn't do.

Usage: python3 -I seed_support_dataset.py <ingest url> <api key>
"""

import json
import sys
import urllib.request

BASE, KEY = sys.argv[1].rstrip("/"), sys.argv[2]

ITEMS = [
    {
        "input": {"question": "Where is order 1042?", "client": None},
        "expected_output": "UPS",
        "expected_trajectory": [{"tool": "lookup_order", "args": {"order_id": "1042"}}],
    },
    {
        "input": {"question": "I'd like to return order 1043, I changed my mind.", "client": None},
        "expected_trajectory": [
            {"tool": "lookup_order", "args": {"order_id": "1043"}},
            {"tool": "issue_refund", "args": {"order_id": "1043"}},
        ],
    },
    {
        "input": {"question": "Can I get a refund on order 1044?", "client": None},
        "expected_output": "30",
        "expected_trajectory": [{"tool": "lookup_order", "args": {"order_id": "1044"}}],
    },
    {
        "input": {"question": "Where is order 1045?", "client": None},
        "expected_trajectory": [{"tool": "escalate"}],
        "metadata": {"note": "fails on purpose: a status question shouldn't be escalated"},
    },
]


def call(method: str, path: str, body=None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or b"null")


ds = call("POST", "/v1/datasets", {"name": "support", "description": "demo agent evals"})
for item in ITEMS:
    call("POST", f"/v1/datasets/{ds['id']}/items", item)
print(json.dumps({"dataset_id": ds["id"], **call("POST", f"/v1/datasets/{ds['id']}/publish")}))
