"""`agenttrace eval`: run the agent over a dataset version and upload the scores (ADR-0006).

After a completed run it prints the comparison with the baseline (ADR-0024). Exit codes: 0 when
the experiment completed, 1 when it failed (or, with `--fail-on-regression`, when it couldn't be
compared), 2 when the run couldn't start (bad eval.yaml, unknown dataset, import error; nothing is
recorded), and 3 with `--fail-on-regression` when a scorer regressed against the baseline
(ADR-0025). No baseline isn't a regression.
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


def merge_base(commit: str, base: str) -> str | None:
    """Where `commit` branched from `base`, preferring the remote's copy of the branch, which is
    what CI checks out (ADR-0026). None in a shallow clone without the history."""
    return _git("merge-base", commit, f"origin/{base}") or _git("merge-base", commit, base)


def _write(path: str, text: str) -> None:
    try:
        Path(path).write_text(text)
    except OSError as e:
        print(f"agenttrace eval: can't write {path}: {e.strerror}", file=sys.stderr)


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
    from agenttrace.eval import config, report, scorers
    from agenttrace.eval.client import ApiError, Client
    from agenttrace.eval.runner import Git, RunError, Runner, StartError, git_from_env

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
    commit = git.commit or _git("rev-parse", "HEAD")
    base = args.base or git.base_branch
    base_commit = merge_base(commit or "HEAD", base) if base else None
    if base and base_commit is None:
        print(
            f"agenttrace eval: no merge-base with {base} (a shallow clone?); the baseline will be "
            f"the newest run on {base}",
            file=sys.stderr,
        )
    git = Git(
        commit=commit,
        branch=git.branch or _git("rev-parse", "--abbrev-ref", "HEAD"),
        merge_base=base_commit,
        base_branch=base,
    )
    print(
        f"running {cfg.entry} over {version.name} v{version.version}: "
        f"{len(version.items)} items x {cfg.samples} samples",
        file=sys.stderr,
    )
    runner = Runner(cfg, client, entry, built, report=lambda line: print(line, file=sys.stderr))
    try:
        result = runner.run(version, git, args.name)
    except StartError as e:
        return stop(str(e))
    except RunError as e:
        print(f"agenttrace eval: {e}", file=sys.stderr)
        if args.report_md:
            _write(args.report_md, report.failed_markdown(str(e)))
        if e.abandoned:  # agent threads may still be running; exiting normally would wait
            _exit_now(1)
        return 1
    print(summary(result.summary))
    code = 0
    try:
        cmp = client.comparison(result.experiment_id)
    except ApiError as e:
        print(f"agenttrace eval: couldn't compare with a baseline: {e}", file=sys.stderr)
        if args.report_md:
            _write(args.report_md, report.failed_markdown(f"couldn't compare: {e}"))
        code = 1 if args.fail_on_regression else 0
    else:
        print(report.text(cmp))
        if args.report_md:
            md = report.markdown(result.summary, cmp, version.name, args.web_url)
            _write(args.report_md, md)
        if args.fail_on_regression and cmp["regressions"]:
            code = 3
    if result.abandoned:  # a hung sync sample: don't wait for its thread at exit
        _exit_now(code)
    return code


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
    ev.add_argument(
        "--base",
        help="the branch this change targets, to find a baseline when not in a GitHub pull request",
    )
    ev.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="exit 3 when a scorer regresses against the baseline",
    )
    ev.add_argument("--report-md", help="write the comparison as markdown, for a PR comment")
    ev.add_argument(
        "--web-url",
        default=os.environ.get("AGENTTRACE_WEB_URL"),
        help="the web UI's address, for links in the report (default: $AGENTTRACE_WEB_URL)",
    )
    args = parser.parse_args(argv)
    sys.exit(run_eval(args))


if __name__ == "__main__":
    main()
