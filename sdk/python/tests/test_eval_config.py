import json

import pytest

from agenttrace.eval import config

BASE = {"entry": "pkg.mod:fn", "dataset": "support", "scorers": [{"name": "a", "type": "contains"}]}


def parse(**over):
    return config.parse({**BASE, **over})


def test_defaults_and_scorer_specs():
    cfg = parse(
        scorers=[
            {"name": "a", "type": "contains"},
            {"name": "steps", "type": "trajectory", "mode": "forbidden", "tools": ["escalate"]},
        ]
    )
    assert (cfg.version, cfg.samples, cfg.concurrency, cfg.timeout_s) == ("latest", 3, 4, 120)
    a, steps = (s.spec() for s in cfg.scorers)
    assert (a.type, a.threshold, a.config) == ("contains", 1.0, {"case_sensitive": False})
    assert steps.config == {"mode": "forbidden", "tools": ["escalate"]}  # unset fields left out


@pytest.mark.parametrize(
    "over, where",
    [
        ({"scorers": [{"name": "a", "type": "contains", "case_sensitve": True}]}, "case_sensitve"),
        ({"scorers": [{"name": "a", "type": "nope"}]}, "scorers.0"),
        ({"scorers": [{"name": "t", "type": "trajectory", "mode": "forbidden"}]}, "tools"),
        ({"scorers": [{"name": "t", "type": "trajectory", "mode": "exact", "max": 3}]}, "max"),
        ({"scorers": [{"name": "Has Space", "type": "contains"}]}, "name"),
        (
            {"scorers": [{"name": "a", "type": "contains"}, {"name": "a", "type": "regex"}]},
            "unique",
        ),
        ({"entry": "no_colon"}, "entry"),
        ({"version": 0}, "version"),
        ({"scorers": []}, "scorers"),
        ({"surprise": 1}, "surprise"),
    ],
)
def test_mistakes_are_errors_that_say_where(over, where):
    with pytest.raises(config.ConfigError) as e:
        parse(**over)
    assert where in str(e.value)


def test_hash_ignores_formatting_defaults_name_and_concurrency(tmp_path):
    a = tmp_path / "a.yaml"
    a.write_text("entry: pkg.mod:fn\ndataset: support\nscorers:\n  - {name: a, type: contains}\n")
    b = tmp_path / "b.yaml"
    b.write_text(
        "# a comment\nscorers: [{type: contains, name: a, case_sensitive: false}]\n"
        "dataset: support\nentry: 'pkg.mod:fn'\nsamples: 3\nname: tuned\nconcurrency: 16\n"
    )
    assert config.load(a).hash() == config.load(b).hash()
    assert parse().hash() != parse(samples=5).hash()
    changed = [{"name": "a", "type": "contains", "threshold": 0.5}]
    assert parse().hash() != parse(scorers=changed).hash()


def test_the_dataset_version_isnt_hashed():
    """Comparisons use the items both runs share (ADR-0007), so pinning or overriding the
    version must not make runs incomparable."""
    latest = parse()
    assert parse(version=3).hash() == latest.hash()
    overridden = latest.with_version(7)
    assert overridden.version == 7 and overridden.hash() == latest.hash()
    with pytest.raises(ValueError):
        latest.with_version(0)


def test_schema_files_are_read_relative_to_eval_yaml_and_hashed(tmp_path):
    (tmp_path / "schemas").mkdir()
    schema = tmp_path / "schemas" / "answer.json"
    schema.write_text(json.dumps({"type": "object"}))
    path = tmp_path / "eval.yaml"
    path.write_text(
        "entry: pkg.mod:fn\ndataset: support\nscorers:\n"
        "  - {name: shape, type: json_schema, schema_file: schemas/answer.json}\n"
    )
    cfg = config.load(path)
    assert cfg.scorers[0].spec().config == {
        "schema": {"type": "object"},
        "schema_file": "schemas/answer.json",
    }
    before = cfg.hash()
    schema.write_text(json.dumps({"type": "string"}))
    assert config.load(path).hash() != before

    both = {"name": "s", "type": "json_schema", "schema": {}, "schema_file": "x.json"}
    with pytest.raises(config.ConfigError, match="not both"):
        parse(scorers=[both])
    with pytest.raises(config.ConfigError, match="can't read"):
        config.parse(
            {**BASE, "scorers": [{"name": "s", "type": "json_schema", "schema_file": "nope.json"}]},
            base=tmp_path,
        )


def test_unreadable_files_are_config_errors(tmp_path):
    with pytest.raises(config.ConfigError, match="can't read"):
        config.load(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("entry: [unclosed")
    with pytest.raises(config.ConfigError, match="isn't valid YAML"):
        config.load(bad)
