"""`agenttrace eval`: run the agent over a dataset version and upload the scores (ADR-0006).

Exit codes: 0 when the experiment completed (whatever the scores: Phase 3 has no gate), 1 when
it failed, 2 when the run couldn't start (bad eval.yaml, unknown dataset, import error); nothing
is recorded in that case.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from agenttrace.exporter import Config, flush


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = out.stdout.strip()
    return value if out.returncode == 0 and value and value != "HEAD" else None


def _pct(passed: int, scored: int) -> str:
    return f"{passed}/{scored} ({passed / scored:.0%})" if scored else "n/a"


def summary(detail: dict[str, Any]) -> str:
    cost = f"${detail['cost_usd']:.4f}"
    if detail["unpriced_samples"]:
        cost += f" + {detail['unpriced_samples']} unpriced samples"
    lines = [
        f"experiment {detail['id']}  {detail['status']}  "
        f"{detail['samples_uploaded']} samples over {detail['item_count']} items  cost {cost}",
    ]
    width = max(len(s["scorer"]) for s in detail["scorer_stats"])
    for s in detail["scorer_stats"]:
        spread = " ".join("-" if r is None else f"{r:.0%}" for r in s["pass_rate_by_sample"])
        lines.append(f"  {s['scorer']:<{width}}  {_pct(s['passed'], s['scored']):>14}  {spread}")
    failing = [
        (i["item_id"][:8], name, sc)
        for i in detail["items"]
        for name, sc in i["scores"].items()
        if sc["passed"] * 2 <= sc["scored"]  # most samples failed
    ]
    if failing:
        lines.append("items failing most samples:")
        lines += [f"  {item}  {name} {sc['passed']}/{sc['scored']}" for item, name, sc in failing]
    errored = sum(i["errored"] for i in detail["items"])
    if errored:
        lines.append(f"{errored} samples errored (the agent raised or timed out)")
    return "\n".join(lines)


def run_eval(args: argparse.Namespace) -> int:
    from agenttrace.eval import config, scorers
    from agenttrace.eval.client import ApiError, Client
    from agenttrace.eval.runner import Git, RunError, Runner, git_from_env

    def stop(message: str) -> int:
        print(f"agenttrace eval: {message}", file=sys.stderr)
        return 2

    try:
        cfg = config.load(args.config)
    except config.ConfigError as e:
        return stop(str(e))
    if args.dataset_version is not None:
        try:
            cfg = cfg.with_version(args.dataset_version)
        except ValueError as e:  # pydantic's ValidationError, e.g. version 0
            return stop(f"--dataset-version: {e}")
    env = Config.from_env()
    if not env.api_key:
        return stop("set AGENTTRACE_API_KEY to the project's API key")
    # The entry point and custom scorers are imported like a script run from here would.
    for path in (str(Path.cwd()), str(Path(args.config).resolve().parent)):
        if path not in sys.path:
            sys.path.insert(0, path)
    client = Client(env.endpoint, env.api_key)
    try:
        version = client.dataset_version(cfg.dataset, cfg.version)
        entry = scorers.load_object(cfg.entry)
        if not callable(entry):
            return stop(f"{cfg.entry} isn't callable")
        built = scorers.build(cfg)
    except (ApiError, scorers.ScorerError) as e:
        return stop(str(e))

    git = git_from_env(dict(os.environ))
    git = Git(
        commit=git.commit or _git("rev-parse", "HEAD"),
        branch=git.branch or _git("rev-parse", "--abbrev-ref", "HEAD"),
    )
    print(
        f"running {cfg.entry} over {version.name} v{version.version}: "
        f"{len(version.items)} items x {cfg.samples} samples",
        file=sys.stderr,
    )
    runner = Runner(cfg, client, entry, built, report=lambda line: print(line, file=sys.stderr))
    try:
        result = runner.run(version, git, args.name)
    except RunError as e:
        print(f"agenttrace eval: {e}", file=sys.stderr)
        if e.abandoned:  # agent threads may still be running; exiting normally would wait
            _exit_now(1)
        return 1
    print(summary(result.summary))
    if result.abandoned:  # a hung sync sample: don't wait for its thread at exit
        _exit_now(0)
    return 0


def _exit_now(code: int) -> None:
    """Exit without joining agent threads that may never return (a hung sample)."""
    sys.stdout.flush()
    sys.stderr.flush()
    flush(timeout_s=2)
    os._exit(code)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="agenttrace")
    sub = parser.add_subparsers(dest="command", required=True)
    ev = sub.add_parser("eval", help="run eval.yaml over a dataset version and upload the scores")
    ev.add_argument("-c", "--config", default="eval.yaml", help="path to eval.yaml")
    ev.add_argument("--name", help="a name for this experiment")
    ev.add_argument("--dataset-version", type=int, help="run this version instead of eval.yaml's")
    args = parser.parse_args(argv)
    sys.exit(run_eval(args))


if __name__ == "__main__":
    main()
