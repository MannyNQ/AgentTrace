"""`eval.yaml` (GLOSSARY.md): what to run, over which dataset version, scored how.

```yaml
entry: demo_agent.evals:eval_entry   # called with each item's input (ADR-0022)
dataset: support                     # a dataset name or id
version: latest                      # or a number; resolved when the run starts
samples: 3
concurrency: 4
timeout_s: 120
scorers:
  - {name: answer, type: contains}
  - {name: steps, type: trajectory, mode: in_order}
  - {name: no-refund, type: trajectory, mode: forbidden, tools: [issue_refund]}
  - {name: helpful, type: llm_judge, model: claude-haiku-5-5, criteria: "Answers the question."}
```

Every scorer also takes `threshold` (the pass threshold, default 1.0) and `max_drop` (the largest
pass-rate drop versus the baseline the CI gate tolerates, default 0.05; ADR-0025).

Each scorer type has its own fields, and unknown fields are errors, so a typo can't silently
change what's graded. The config hash decides which experiments can be compared (ADR-0007).
"""

import hashlib
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    model_validator,
)

from trayce.eval.results import ScorerName, ScorerSpec

ImportPath = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")]
Threshold = Annotated[float, Field(ge=0, le=1)]
# Fields that don't change what's measured, so changing them keeps experiments comparable. The
# dataset version is one: comparisons already use the items both runs share (ADR-0007), and
# `latest` would otherwise hash the same across versions while a pinned number wouldn't.
NOT_HASHED = {"name", "concurrency", "version"}
# Per scorer: loosening the CI gate doesn't change what's measured either (ADR-0025).
NOT_HASHED_SCORER = {"max_drop"}
JudgeField = Literal["input", "output", "expected_output", "trajectory"]


class ConfigError(Exception):
    """eval.yaml can't be read or is invalid; the message says where."""


class _Scorer(BaseModel):
    """Each scorer adds `type`, a Literal naming it, and its own fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
    name: ScorerName
    threshold: Threshold = 1.0
    max_drop: Threshold = 0.05

    def spec(self) -> ScorerSpec:
        """The scorer as the server stores it: its type-specific fields go in `config`."""
        fields = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"name", "threshold", "max_drop"},
            exclude_none=True,
        )
        kind = fields.pop("type")
        return ScorerSpec(
            name=self.name,
            type=kind,
            threshold=self.threshold,
            max_drop=self.max_drop,
            config=fields,
        )


class ExactMatch(_Scorer):
    """The output equals `expected_output` (JSON equality; strings compared without surrounding
    whitespace)."""

    type: Literal["exact_match"]
    case_sensitive: bool = True


class Contains(_Scorer):
    """The output, as text, contains `expected_output`, or each of them when it's a list."""

    type: Literal["contains"]
    case_sensitive: bool = False


class Regex(_Scorer):
    """The output, as text, matches `pattern` (every item), or the item's `expected_output` when
    no pattern is given."""

    type: Literal["regex"]
    pattern: str | None = None


class JsonSchema(_Scorer):
    """The output (parsed first if it's a JSON string) is valid against `schema`, given inline or
    as `schema_file`, a JSON file relative to eval.yaml. A file is read when the config loads, so
    its contents are part of the config hash. Applies to every item."""

    type: Literal["json_schema"]
    schema_: Annotated[dict[str, Any], Field(alias="schema")]
    schema_file: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _read_schema_file(cls, data: Any, info: ValidationInfo) -> Any:
        if not isinstance(data, dict) or "schema_file" not in data:
            return data
        if "schema" in data:
            raise ValueError("give schema or schema_file, not both")
        base = Path((info.context or {}).get("base", "."))
        path = base / data["schema_file"]
        try:
            schema = json.loads(path.read_text())
        except OSError as e:
            raise ValueError(f"can't read {path}: {e.strerror}") from e
        except json.JSONDecodeError as e:
            raise ValueError(f"{path} isn't valid JSON: {e}") from e
        return {**data, "schema": schema}


class Trajectory(_Scorer):
    """Grades the tool calls (GLOSSARY.md, trajectory scorer). `exact`, `in_order` and
    `any_order` compare against the item's expected trajectory; `forbidden` fails on any call to
    `tools`, and `max_steps` on more than `max` tool calls, for every item."""

    type: Literal["trajectory"]
    mode: Literal["exact", "in_order", "any_order", "forbidden", "max_steps"]
    tools: list[str] | None = None
    max: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def _params(self) -> "Trajectory":
        if (self.mode == "forbidden") != (self.tools is not None):
            raise ValueError("`tools` goes with mode forbidden, and only with it")
        if (self.mode == "max_steps") != (self.max is not None):
            raise ValueError("`max` goes with mode max_steps, and only with it")
        return self


class Custom(_Scorer):
    """A Python function `module:name`, called with a `Case`. See `trayce.eval.scorers`."""

    type: Literal["custom"]
    function: ImportPath


class LlmJudge(_Scorer):
    """An Anthropic model grades the sample against `criteria`, pass or fail with a reason
    (ADR-0027). `include` picks what it's shown; by default the input, the output, and the
    expected output when the item has one. `model` has no default: it's part of the hash, and a
    default that changed would silently reset baselines."""

    type: Literal["llm_judge"]
    model: Annotated[str, StringConstraints(min_length=1)]
    criteria: Annotated[str, StringConstraints(min_length=1)]
    include: Annotated[list[JudgeField], Field(min_length=1)] = [
        "input",
        "output",
        "expected_output",
    ]

    @model_validator(mode="after")
    def _output_shown(self) -> "LlmJudge":
        if len(set(self.include)) != len(self.include):
            raise ValueError("`include` lists a field twice")
        if not {"output", "trajectory"} & set(self.include):
            raise ValueError("`include` needs output or trajectory: something the agent did")
        return self


Scorer = Annotated[
    ExactMatch | Contains | Regex | JsonSchema | Trajectory | Custom | LlmJudge,
    Field(discriminator="type"),
]


class EvalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    entry: ImportPath
    dataset: Annotated[str, StringConstraints(min_length=1)]
    version: Literal["latest"] | Annotated[int, Field(ge=1)] = "latest"
    name: str | None = None
    samples: Annotated[int, Field(ge=1, le=100)] = 3
    concurrency: Annotated[int, Field(ge=1, le=64)] = 4
    timeout_s: Annotated[float, Field(gt=0)] = 120.0
    scorers: Annotated[list[Scorer], Field(min_length=1)]

    @model_validator(mode="after")
    def _unique_names(self) -> "EvalConfig":
        names = [s.name for s in self.scorers]
        if len(names) != len(set(names)):
            raise ValueError("scorer names must be unique")
        return self

    def with_version(self, version: int) -> "EvalConfig":
        """The same config run on another dataset version (`trayce eval --dataset-version`).
        Validated like eval.yaml, and hashed the same as the original."""
        return EvalConfig.model_validate({**self.as_json(), "version": version})

    def as_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)

    def hash(self) -> str:
        """SHA-256 of the config as canonical JSON, defaults filled in and `NOT_HASHED` and
        `NOT_HASHED_SCORER` left out, so formatting, comments, spelled-out defaults, the dataset
        version, and `max_drop` don't change it (ADR-0023, ADR-0025)."""
        data = {k: v for k, v in self.as_json().items() if k not in NOT_HASHED}
        data["scorers"] = [
            {k: v for k, v in s.items() if k not in NOT_HASHED_SCORER} for s in data["scorers"]
        ]
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()


def _where(e: ValidationError) -> str:
    lines = []
    for err in e.errors(include_url=False):
        loc = ".".join(str(p) for p in err["loc"]) or "eval.yaml"
        lines.append(f"  {loc}: {err['msg']}")
    return "\n".join(lines)


def parse(data: Any, source: str = "eval.yaml", base: Path | None = None) -> EvalConfig:
    """`base` is the directory that relative paths in the config, like `schema_file`, start from."""
    try:
        return EvalConfig.model_validate(data, context={"base": base or Path(".")})
    except ValidationError as e:
        raise ConfigError(f"{source} is invalid:\n{_where(e)}") from e


def load(path: str | Path) -> EvalConfig:
    try:
        import yaml
    except ImportError as e:  # the eval extra isn't installed
        raise ConfigError("reading eval.yaml needs PyYAML: pip install 'trayce[eval]'") from e
    p = Path(path)
    try:
        data = yaml.safe_load(p.read_text())
    except OSError as e:
        raise ConfigError(f"can't read {p}: {e.strerror}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"{p} isn't valid YAML: {e}") from e
    return parse(data, str(p), p.parent)
