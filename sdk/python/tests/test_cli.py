import pytest

from trayce import cli


def run(monkeypatch, tmp_path, yaml_text: str | None, env: dict[str, str]) -> int:
    for k in ("TRAYCE_API_KEY", "TRAYCE_ENDPOINT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    path = tmp_path / "eval.yaml"
    if yaml_text is not None:
        path.write_text(yaml_text)
    with pytest.raises(SystemExit) as e:
        cli.main(["eval", "-c", str(path)])
    return int(e.value.code or 0)


GOOD = "entry: pkg.mod:fn\ndataset: support\nscorers: [{name: a, type: contains}]\n"


def test_problems_before_the_run_exit_2_and_record_nothing(monkeypatch, tmp_path, capsys):
    assert run(monkeypatch, tmp_path, None, {}) == 2
    assert "can't read" in capsys.readouterr().err
    assert run(monkeypatch, tmp_path, "entry: x\n", {"TRAYCE_API_KEY": "k"}) == 2
    assert (
        "eval.yaml is invalid" in capsys.readouterr().err or "is invalid" in capsys.readouterr().err
    )
    assert run(monkeypatch, tmp_path, GOOD, {}) == 2
    assert "TRAYCE_API_KEY" in capsys.readouterr().err
    monkeypatch.setenv("TRAYCE_API_KEY", "k")
    with pytest.raises(SystemExit) as e:
        cli.main(["eval", "-c", str(tmp_path / "eval.yaml"), "--dataset-version", "0"])
    assert e.value.code == 2 and "--dataset-version" in capsys.readouterr().err
    # Nothing listening: the dataset lookup fails before any experiment is created.
    env = {"TRAYCE_API_KEY": "k", "TRAYCE_ENDPOINT": "http://127.0.0.1:9"}
    monkeypatch.setattr("trayce.eval.client.time.sleep", lambda s: None)
    assert run(monkeypatch, tmp_path, GOOD, env) == 2
    assert "couldn't reach" in capsys.readouterr().err


def test_summary_lists_rates_spread_and_failing_items():
    detail = {
        "id": "e1",
        "status": "completed",
        "samples_uploaded": 6,
        "item_count": 2,
        "cost_usd": 0.0123,
        "unpriced_samples": 1,
        "scorer_stats": [
            {"scorer": "answer", "passed": 1, "scored": 3, "pass_rate_by_sample": [1.0, 0.0, None]},
            {"scorer": "steps", "passed": 0, "scored": 0, "pass_rate_by_sample": [None]},
        ],
        "items": [
            {
                "item_id": "aaaaaaaa-1",
                "errored": 1,
                "scores": {"answer": {"passed": 1, "scored": 3}},
            },
            {"item_id": "bbbbbbbb-2", "errored": 0, "scores": {}},
        ],
    }
    text = cli.summary(detail)
    assert "cost $0.0123 + 1 unpriced samples" in text
    rows = [line.split() for line in text.splitlines()]
    assert ["answer", "1/3", "(33%)", "100%", "0%", "-"] in rows
    assert ["steps", "n/a", "-"] in rows
    assert "aaaaaaaa  answer 1/3" in text
    assert "1 samples errored" in text
