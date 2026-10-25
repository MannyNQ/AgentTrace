import json
from datetime import date

import pytest

from demo_agent.tools import ToolError, issue_refund, run_tool, search_kb

TODAY = date(2026, 10, 25)


def test_lookup_order_via_run_tool():
    out, is_error = run_tool("lookup_order", {"order_id": "1042"})
    assert not is_error
    assert json.loads(out)["carrier"] == "UPS"


def test_unknown_order_is_a_tool_error_not_a_crash():
    out, is_error = run_tool("lookup_order", {"order_id": "9999"})
    assert is_error and "9999" in out


def test_unknown_tool():
    assert run_tool("delete_everything", {}) == ("Unknown tool 'delete_everything'.", True)


def test_refund_within_window():
    assert issue_refund("1043", "broken", today=TODAY)["refunded_usd"] == 58.0


@pytest.mark.parametrize(
    ("order_id", "why"), [("1044", "outside the 30-day"), ("1042", "not delivered")]
)
def test_refund_refused(order_id, why):
    with pytest.raises(ToolError, match=why):
        issue_refund(order_id, "changed mind", today=TODAY)


def test_search_kb_finds_refund_policy():
    assert search_kb("how do refunds work")[0]["id"] == "refund-policy"
