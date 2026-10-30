import pytest

from trayce.eval import config, scorers
from trayce.eval.results import ToolCall
from trayce.eval.scorers import Case


def build(**scorer):
    cfg = config.parse({"entry": "m:f", "dataset": "d", "scorers": [{"name": "s", **scorer}]})
    return scorers.build(cfg)[0]


def calls(*spec) -> list[ToolCall]:
    return [ToolCall(tool=t, args=a) for t, a in spec]


def test_exact_match():
    s = build(type="exact_match")
    assert not s.applies(Case(input="q"))
    assert s.grade(Case(input="q", expected_output="Shipped", output=" Shipped\n")).score == 1
    miss = s.grade(Case(input="q", expected_output="Shipped", output="shipped"))
    assert miss.score == 0 and miss.reason == "expected Shipped, got shipped"
    loose = build(type="exact_match", case_sensitive=False)
    assert loose.grade(Case(input="q", expected_output="Shipped", output="shipped")).score == 1
    assert s.grade(Case(input="q", expected_output={"a": [1]}, output={"a": [1]})).score == 1


def test_contains_checks_every_listed_phrase():
    s = build(type="contains")
    case = Case(input="q", expected_output=["UPS", "october 28"], output="Via UPS, due October 28.")
    assert s.grade(case).score == 1
    miss = s.grade(Case(input="q", expected_output=["UPS", "FedEx"], output="Via UPS"))
    assert miss.reason == "output doesn't contain fedex"


def test_regex_from_config_or_from_the_item():
    fixed = build(type="regex", pattern=r"\bHUMAN-\d+\b")
    assert fixed.applies(Case(input="q"))
    assert fixed.grade(Case(input="q", output="ticket HUMAN-0001")).score == 1
    per_item = build(type="regex")
    assert not per_item.applies(Case(input="q", expected_output=3))
    assert per_item.grade(Case(input="q", expected_output="^\\d+$", output="42")).score == 1
    bad = per_item.grade(Case(input="q", expected_output="(", output="x"))
    assert "isn't a valid regex" in (bad.reason or "")
    with pytest.raises(scorers.ScorerError, match="bad pattern"):
        build(type="regex", pattern="(")


def test_json_schema():
    s = build(type="json_schema", schema={"type": "object", "required": ["refund"]})
    assert s.grade(Case(input="q", output='{"refund": 1}')).score == 1
    assert s.grade(Case(input="q", output="not json")).reason == "output isn't JSON"
    assert "'refund' is a required property" in (s.grade(Case(input="q", output={})).reason or "")
    with pytest.raises(scorers.ScorerError, match="invalid schema"):
        build(type="json_schema", schema={"type": 12})


ORDER = calls(("lookup_order", {"order_id": "1042", "verbose": True}), ("search_kb", None),
              ("issue_refund", {"order_id": "1042", "reason": "broken"}))  # fmt: skip


@pytest.mark.parametrize(
    "mode, expected, passes, reason",
    [
        ("exact", [("lookup_order", {"order_id": "1042"}), ("search_kb", None),
                   ("issue_refund", None)], True, None),
        ("exact", [("lookup_order", None), ("issue_refund", None)], False,
         "call 2: expected issue_refund, got search_kb"),
        ("exact", [("lookup_order", None), ("search_kb", None)], False,
         "expected 2 tool calls, got 3"),
        ("in_order", [("lookup_order", None), ("issue_refund", {"order_id": "1042"})], True, None),
        ("in_order", [("issue_refund", None), ("lookup_order", None)], False,
         "expected lookup_order after issue_refund; not called in order"),
        ("in_order", [("lookup_order", {"order_id": "9"})], False,
         'expected lookup_order({"order_id": "9"}); not called in order'),
        ("any_order", [("issue_refund", None), ("lookup_order", None)], True, None),
        ("any_order", [("escalate", None)], False, "no call matched escalate"),
        ("any_order", [("search_kb", None), ("search_kb", None)], False,
         "too few distinct calls to match every expected call"),
    ],
)  # fmt: skip
def test_trajectory_against_the_expected_calls(mode, expected, passes, reason):
    s = build(type="trajectory", mode=mode)
    case = Case(input="q", expected_trajectory=calls(*expected), trajectory=ORDER)
    result = s.grade(case)
    assert (result.score == 1, result.reason) == (passes, reason)


def test_any_order_backtracks_over_partial_arguments():
    s = build(type="trajectory", mode="any_order")
    actual = calls(("lookup_order", {"order_id": "1"}), ("lookup_order", {"order_id": "2"}))
    expected = calls(("lookup_order", None), ("lookup_order", {"order_id": "1"}))
    assert s.grade(Case(input="q", expected_trajectory=expected, trajectory=actual)).score == 1


def test_trajectory_modes_that_apply_to_every_item():
    no_item_expectation = Case(input="q", trajectory=ORDER)
    assert not build(type="trajectory", mode="in_order").applies(no_item_expectation)
    forbidden = build(type="trajectory", mode="forbidden", tools=["issue_refund", "escalate"])
    assert forbidden.applies(no_item_expectation)
    assert (
        forbidden.grade(no_item_expectation).reason == "called forbidden tool issue_refund (call 3)"
    )
    limit = build(type="trajectory", mode="max_steps", max=2)
    assert limit.grade(no_item_expectation).reason == "3 tool calls; the limit is 2"
    assert build(type="trajectory", mode="max_steps", max=3).grade(no_item_expectation).score == 1


def polite(case: Case):
    if case.output is None:
        return None
    return (0.5, "half polite") if "please" in case.output else "please" in case.output


def broken(case: Case):
    return 7


def test_custom_scorers(monkeypatch):
    s = build(type="custom", function=f"{__name__}:polite")
    assert s.grade(Case(input="q", output=None)) is None
    assert s.grade(Case(input="q", output="please")) == scorers.Result(0.5, "half polite")
    assert s.grade(Case(input="q", output="no")) == scorers.Result(0.0, None)
    with pytest.raises(scorers.ScorerError, match="not a score"):
        build(type="custom", function=f"{__name__}:broken").grade(Case(input="q"))
    with pytest.raises(scorers.ScorerError, match="can't import"):
        build(type="custom", function="no_such_module:fn")
    with pytest.raises(scorers.ScorerError, match="has no"):
        build(type="custom", function=f"{__name__}:missing")


def test_long_reasons_are_cut():
    r = build(type="exact_match").grade(Case(input="q", expected_output="a", output="b" * 1000))
    assert len(r.reason or "") <= scorers.MAX_REASON
