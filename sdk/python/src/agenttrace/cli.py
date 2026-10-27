"""`agenttrace eval`: run the agent over a dataset version and upload the scores (ADR-0006).
`agenttrace replay`: run it again on a recorded trace's input (ADR-0028), or fork it from a step
or stub its tools (ADR-0040); see `run_replay`.

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
            md = report.markdown(
                result.summary, cmp, version.name, _project_url(client, args.web_url)
            )
            _write(args.report_md, md)
        if args.fail_on_regression and cmp["regressions"]:
            code = 3
    if result.abandoned:  # a hung sync sample: don't wait for its thread at exit
        _exit_now(code)
    return code


def _dirty() -> bool | None:
    """Whether the working tree has uncommitted changes; None without git."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return bool(out.stdout.strip()) if out.returncode == 0 else None


def run_replay(args: argparse.Namespace) -> int:
    """Exit codes as `eval`'s: 0 when the replay completed, 1 when it failed, 2 when it couldn't
    start (nothing recorded). With `--require-pass`, 3 when a scored run didn't pass or no run
    was graded, and 2 before running when there's nothing to score (ADR-0030)."""
    from agenttrace.eval import config, fork, scorers
    from agenttrace.eval import replay as rp
    from agenttrace.eval.client import ApiError, Client
    from agenttrace.eval.results import MAX_SAMPLES_PER_ITEM
    from agenttrace.eval.runner import Git, RunError, StartError, git_from_env

    def stop(message: str) -> int:
        print(f"agenttrace replay: {message}", file=sys.stderr)
        return 2

    path = args.config or "eval.yaml"
    cfg = None
    if args.config or Path(path).exists() or not args.entry:
        try:
            cfg = config.load(path)
        except config.ConfigError as e:
            hint = "" if args.entry or args.config else "; or pass --entry module:function"
            return stop(f"{e}{hint}")
    runs = args.samples if args.samples is not None else (cfg.samples if cfg else rp.DEFAULT_RUNS)
    if not 1 <= runs <= MAX_SAMPLES_PER_ITEM:
        return stop(f"--samples must be between 1 and {MAX_SAMPLES_PER_ITEM}")
    env = Config.from_env()
    if not env.api_key:
        return stop("set AGENTTRACE_API_KEY to the project's API key")
    for p in (str(Path.cwd()), str(Path(path).resolve().parent)):
        if p not in sys.path:
            sys.path.insert(0, p)
    client = Client(env.endpoint, env.api_key)
    entry_path = args.entry or (cfg.entry if cfg else "")
    try:
        o = rp.original(client.trace_input(args.trace_id))
        entry = scorers.load_object(entry_path)
        if not callable(entry):
            return stop(f"{entry_path} isn't callable")
        how = rp.scoring(cfg, client, o, lambda: scorers.build(cfg) if cfg else [])
        plan = None
        if args.fork_from is not None or args.stub_tools:
            steps = client.trace_steps(args.trace_id)
            plan = fork.plan(args.trace_id, steps, args.fork_from, args.stub_tools)
    except (ApiError, scorers.ScorerError, fork.ForkError) as e:
        return stop(str(e))
    if args.require_pass and how.unscored_reason:
        return stop(f"--require-pass needs something to score, but {how.unscored_reason}")

    env_git = git_from_env(dict(os.environ))
    git = Git(
        commit=env_git.commit or _git("rev-parse", "HEAD"),
        branch=env_git.branch or _git("rev-parse", "--abbrev-ref", "HEAD"),
    )
    if plan is not None and plan.fork is not None:
        print(
            f"forking trace {args.trace_id} at {plan.fork.label}: {plan.fork.index} steps played "
            "back, live from there",
            file=sys.stderr,
        )
    elif o.ref.trace_id != args.trace_id:
        print(f"{args.trace_id} is a replay run; replaying its original", file=sys.stderr)
    if plan is not None and plan.stub_tools:
        print("tools answered from the recording; none will run", file=sys.stderr)
    scored = (
        f"scored against item {how.item.item_id[:8]} rev {how.item.rev}"
        if how.item
        else f"unscored: {how.unscored_reason}"
    )
    print(
        f"replaying trace {o.ref.trace_id} with {entry_path}: {runs} runs, {scored}",
        file=sys.stderr,
    )
    runner = rp.ReplayRunner(
        client,
        entry,
        o,
        how,
        {s.name: s.threshold for s in cfg.scorers} if cfg else {},
        cfg.concurrency if cfg else rp.DEFAULT_CONCURRENCY,
        cfg.timeout_s if cfg else rp.DEFAULT_TIMEOUT_S,
        report=lambda line: print(line, file=sys.stderr),
        plan=plan,
    )
    try:
        result = runner.run(runs, git, _dirty(), cfg)
    except StartError as e:
        return stop(str(e))
    except RunError as e:
        print(f"agenttrace replay: {e}", file=sys.stderr)
        if e.abandoned:
            _exit_now(1)
        return 1
    print(rp.summary(result.summary, _project_url(client, args.web_url)))
    code = 3 if args.require_pass and not rp.all_pass(result.summary) else 0
    if result.abandoned:
        _exit_now(code)
    return code


def _project_url(client, web_url: str | None) -> str | None:
    """Where the key's project lives in the web app, `<web>/p/<slug>`, for links. An ingest from
    before projects were in URLs has no /v1/project: the bare address still works, since the web
    app redirects old paths into the right project."""
    if not web_url:
        return None
    from agenttrace.eval.client import ApiError

    try:
        slug = client.project()["slug"]
    except ApiError:
        return web_url
    return f"{web_url.rstrip('/')}/p/{slug}"


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
    rp = sub.add_parser("replay", help="run the agent again on a recorded trace's input")
    rp.add_argument("trace_id", help="the trace to replay")
    rp.add_argument("-c", "--config", help="path to eval.yaml (default: eval.yaml, if it exists)")
    rp.add_argument("--entry", help="the entry point, module:function (default: eval.yaml's)")
    rp.add_argument(
        "--samples", type=int, help="how many replay runs (default: eval.yaml's samples, else 3)"
    )
    rp.add_argument(
        "--from",
        dest="fork_from",
        metavar="SPAN_ID",
        help="fork: play the trace back up to this LLM or tool span and run live from it",
    )
    rp.add_argument(
        "--stub-tools",
        action="store_true",
        help="answer every tool call from the recording instead of running it",
    )
    rp.add_argument(
        "--require-pass",
        action="store_true",
        help="exit 3 unless every scored run passes; exit 2 if there's nothing to score",
    )
    rp.add_argument(
        "--web-url",
        default=os.environ.get("AGENTTRACE_WEB_URL"),
        help="the web UI's address, for the replay page link (default: $AGENTTRACE_WEB_URL)",
    )
    args = parser.parse_args(argv)
    sys.exit(run_replay(args) if args.command == "replay" else run_eval(args))


if __name__ == "__main__":
    main()
