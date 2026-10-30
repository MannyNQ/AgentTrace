"""Comparisons (ADR-0024, ADR-0025): the statistics on their own, then baseline selection and the
endpoint against the in-memory stores."""

from uuid import UUID, uuid4

import pytest
from test_experiments_api import Api

from trayce_ingest.comparisons import bootstrap, compare_scorer


def items(*counts: tuple[int, int]) -> dict[UUID, tuple[int, int]]:
    return {uuid4(): c for c in counts}


def paired(base: list[tuple[int, int]], cur: list[tuple[int, int]]):
    ids = [uuid4() for _ in base]
    return dict(zip(ids, base, strict=True)), dict(zip(ids, cur, strict=True))


def test_identical_runs_have_a_zero_interval():
    base, cur = paired([(3, 3), (0, 3), (2, 3)], [(3, 3), (0, 3), (2, 3)])
    c, regressed = compare_scorer("s", 0.05, base, cur)
    assert (c.change, c.ci_low, c.ci_high) == (0, 0, 0)
    assert not c.regressed and not c.underpowered and regressed == []


def test_a_large_drop_on_most_items_regresses_even_when_underpowered():
    base, cur = paired([(3, 3)] * 3 + [(0, 3)], [(0, 3)] * 4)
    c, regressed = compare_scorer("steps", 0.05, base, cur)
    assert c.change == pytest.approx(-0.75)
    assert c.ci_high is not None and c.ci_high < 0
    assert c.regressed and c.underpowered  # four items: wide, but the drop is unmistakable
    assert len(regressed) == 3


def test_a_drop_within_max_drop_does_not_regress():
    # 100 items; 3 flip from pass to fail: a real but small drop (3 points < 5).
    base, cur = paired([(1, 1)] * 100, [(0, 1)] * 3 + [(1, 1)] * 97)
    c, _ = compare_scorer("s", 0.05, base, cur)
    assert c.change == pytest.approx(-0.03)
    assert not c.regressed


def test_noise_is_not_significant():
    # Items move both ways between runs; the net change is a small drop.
    base, cur = paired([(2, 3)] * 20 + [(1, 3)] * 20, [(1, 3)] * 22 + [(2, 3)] * 18)
    c, _ = compare_scorer("s", 0.01, base, cur)
    assert c.change is not None and c.change < 0
    assert c.ci_low is not None and c.ci_high is not None and c.ci_low < 0 < c.ci_high
    assert not c.regressed


def test_resampling_by_item_is_wider_than_by_sample():
    """Correlated samples: all three samples of an item move together, so the item-level
    interval must not shrink as if they were independent."""
    rows = [(3, 3, 0, 3)] * 2 + [(0, 3, 0, 3)] * 8  # two of ten items broke completely
    low, high = bootstrap(rows)
    flat = [(1, 1, 0, 1)] * 6 + [(0, 1, 0, 1)] * 24  # the same samples treated as independent
    flat_low, flat_high = bootstrap(flat)
    assert high - low > flat_high - flat_low


def test_the_bootstrap_is_deterministic():
    rows = [(2, 3, 1, 3), (3, 3, 3, 3), (1, 3, 0, 3)]
    assert bootstrap(rows) == bootstrap(rows)


def test_only_items_scored_in_both_runs_are_compared():
    shared = uuid4()
    base = {shared: (3, 3), uuid4(): (3, 3)}
    cur = {shared: (3, 3), uuid4(): (0, 3)}
    c, _ = compare_scorer("judge", 0.05, base, cur)
    assert c.items == 1 and c.change == 0


def test_item_regressions_need_a_strict_majority_both_ways():
    base, cur = paired([(2, 3), (2, 4), (3, 3)], [(1, 3), (0, 4), (2, 4)])
    _, regressed = compare_scorer("s", 0.05, base, cur)
    assert [(r.baseline_passed, r.current_passed) for r in regressed] == [(2, 1)]


def test_no_shared_items_compares_nothing():
    c, regressed = compare_scorer("s", 0.05, items((3, 3)), items((0, 3)))
    assert (c.items, c.change, c.regressed) == (0, None, False) and regressed == []


# --- Baselines and the endpoint --------------------------------------------------------------


@pytest.fixture
def api() -> Api:
    return Api()


def run(api: Api, exact: list[float], **over) -> str:
    """A completed experiment whose `exact` scorer scores each sample as given (answer item)."""
    exp = api.create(**over)["id"]
    api.upload(
        exp,
        *[
            api.sample(api.answer, i, scores=[{"scorer": "exact", "score": s}])
            for i, s in enumerate(exact)
        ],
        *[api.sample(api.steps, i, scores=[{"scorer": "steps", "score": 1.0}]) for i in range(2)],
    )
    api.finish(exp, "completed")
    return exp


def comparison(api: Api, exp: str, baseline: str | None = None, status=200) -> dict:
    query = f"?baseline={baseline}" if baseline else ""
    return api.call("GET", f"/v1/experiments/{exp}/comparison{query}", status=status)


def test_the_merge_base_run_is_preferred_over_the_base_branch(api: Api):
    at_merge_base = run(api, [1.0, 1.0], git_commit="m1", git_branch="main")
    newer_on_main = run(api, [1.0, 1.0], git_commit="m2", git_branch="main")
    pr = run(api, [0.0, 0.0], git_commit="p1", git_merge_base="m1", git_base_branch="main")
    c = comparison(api, pr)
    assert (c["baseline"]["id"], c["baseline_reason"]) == (at_merge_base, "merge_base")
    assert newer_on_main != c["baseline"]["id"]
    exact = next(s for s in c["scorers"] if s["scorer"] == "exact")
    assert (exact["items"], exact["baseline_rate"], exact["current_rate"]) == (1, 1.0, 0.0)
    assert [r["scorer"] for r in c["item_regressions"]] == ["exact"]


def test_the_base_branch_is_the_fallback_and_no_baseline_passes(api: Api):
    on_main = run(api, [1.0, 1.0], git_commit="m2", git_branch="main")
    pr = run(api, [1.0, 1.0], git_merge_base="gone", git_base_branch="main")
    c = comparison(api, pr)
    assert (c["baseline"]["id"], c["baseline_reason"]) == (on_main, "base_branch")
    assert c["regressions"] == 0

    lonely = run(api, [1.0, 1.0], git_base_branch="develop", config_hash="e" * 64)
    c = comparison(api, lonely)
    assert (c["baseline"], c["scorers"], c["regressions"]) == (None, [], 0)
    assert c["current_stats"]["p50_latency_ms"] == 100.0


def test_only_completed_runs_with_the_same_hash_compare(api: Api):
    base = run(api, [1.0, 1.0], git_branch="main")
    running = api.create(git_base_branch="main")["id"]
    err = comparison(api, running, status=409)["detail"]["error"]
    assert "running" in err
    other_hash = run(api, [1.0, 1.0], config_hash="e" * 64)
    assert "hash" in comparison(api, other_hash, base, status=409)["detail"]["error"]
    failed = api.create()["id"]
    api.finish(failed, "failed")
    pr = run(api, [1.0, 1.0])
    assert "failed" in comparison(api, pr, failed, status=409)["detail"]["error"]
    comparison(api, pr, str(uuid4()), status=404)
    c = comparison(api, pr, base)
    assert (c["baseline"]["id"], c["baseline_reason"]) == (base, "chosen")
    # A running run with a completed main run on the base branch is never chosen as a baseline.
    assert comparison(api, pr)["baseline"] is None


def test_items_added_and_removed_between_versions_are_listed(api: Api):
    base = run(api, [1.0, 1.0], git_branch="main")
    items = f"/v1/datasets/{api.dataset}/items"
    added = api.call("POST", items, {"input": "q3", "expected_output": "C"})["item_id"]
    api.call("DELETE", f"{items}/{api.steps}?rev=1", status=204)
    api.call("POST", f"/v1/datasets/{api.dataset}/publish", status=200)
    exp = api.create(dataset_version=2, git_base_branch="main")["id"]
    api.upload(
        exp,
        *[
            api.sample(item, i, scores=[{"scorer": "exact", "score": 1.0}])
            for item in (api.answer, added)
            for i in range(2)
        ],
    )
    api.finish(exp, "completed")
    c = comparison(api, exp)
    assert c["baseline"]["id"] == base
    assert (c["added_items"], c["removed_items"]) == ([added], [api.steps])
    steps = next(s for s in c["scorers"] if s["scorer"] == "steps")
    assert steps["items"] == 0


def test_judge_usage_is_priced_apart_from_the_agent(api: Api):
    exp = api.create()["id"]
    judge = [{"model": "m", "input_tokens": 500_000, "output_tokens": 0}]
    api.upload(exp, api.sample(api.answer, 0, judge_usage=judge))
    detail = api.call("GET", f"/v1/experiments/{exp}")
    assert detail["cost_usd"] == pytest.approx(6.0)  # $4 + $2 for the agent
    assert detail["judge_cost_usd"] == pytest.approx(2.0)
    sample = api.call("GET", f"/v1/experiments/{exp}/items/{api.answer}")["samples"][0]
    assert sample["judge_cost_usd"] == pytest.approx(2.0)


def test_a_completed_pair_is_computed_once(api: Api, monkeypatch):
    base = run(api, [1.0, 1.0], git_commit="m1", git_branch="main")
    pr = run(api, [0.0, 0.0], git_merge_base="m1", git_base_branch="main")
    calls = []
    import fake_experiments

    original = fake_experiments.FakeExperiments.score_rows
    monkeypatch.setattr(
        fake_experiments.FakeExperiments,
        "score_rows",
        lambda self, project, eid: calls.append(eid) or original(self, project, eid),
    )
    first = comparison(api, pr)
    second = comparison(api, pr)
    chosen = comparison(api, pr, base)
    assert len(calls) == 2  # the baseline's scores and this run's, once
    assert first == second and chosen["scorers"] == first["scorers"]
    assert (first["baseline_reason"], chosen["baseline_reason"]) == ("merge_base", "chosen")
