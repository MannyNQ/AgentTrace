"""The rule for which recorded steps a fork can play back (ADR-0040), against the cases the web
app's copy of it is tested with too (web/src/lib/forks.test.ts)."""

import json
from pathlib import Path

import pytest

from agenttrace.eval.fork import Step, why_not

CASES = json.loads((Path(__file__).parent / "fixtures" / "why_not.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["reason"] or "stubbable")
def test_why_not_matches_the_shared_cases(case):
    s = case["step"]
    step = Step.parse({"index": 0, "span_id": "1" * 16, "name": "x", **s})
    assert why_not(step) == case["reason"]
