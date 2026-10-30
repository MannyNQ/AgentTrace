"""The fork recipe end to end (features/fork.md), through the real CLI: record a demo-agent failure
with a broken prompt, fork it with a fixed one at the LLM step that chose the wrong tool, and check
that the step changed, that the played-back tool never ran (a whole replay with the same tool log
is the control), and that the changed prompt was noted.

    TRAYCE_API_KEY=... TRAYCE_ENDPOINT=http://127.0.0.1:18000 \\
        uv run python .claude/skills/verify/bin/fork_demo.py verify-artifacts/<run-id>

Needs ANTHROPIC_API_KEY (in .env); about eight model calls. Writes fork-*.{out,json} to the run dir
and ends with FORK OK or FORK FAIL (exit 1).
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BIN = Path(__file__).resolve().parent
QUESTION = "Has order 1044 been delivered? If it has, refund it."
ATTEMPTS = 3  # the broken prompt usually, not always, takes the two turns the recipe needs


def api(path: str):
    req = urllib.request.Request(
        os.environ["TRAYCE_ENDPOINT"] + path,
        headers={"Authorization": f"Bearer {os.environ['TRAYCE_API_KEY']}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def steps(trace_id: str) -> list[dict]:
    for _ in range(20):  # the worker stores spans a moment after the run flushes them
        try:
            found = api(f"/v1/traces/{trace_id}/steps")["steps"]
            if found and found[-1]["kind"] == "llm":
                return found
        except urllib.error.HTTPError:
            pass
        time.sleep(1)
    raise SystemExit(f"FORK FAIL: trace {trace_id}'s steps never arrived")


def record(out: Path) -> tuple[str, dict]:
    """A failure with a prefix: a tool call, then an LLM step that calls issue_refund."""
    env = {**os.environ, "FORK_PROMPT": "broken", "PYTHONPATH": str(BIN)}
    for attempt in range(ATTEMPTS):
        recorded = subprocess.run(
            [sys.executable, str(BIN / "fork_entry.py"), QUESTION],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        trace = recorded.stdout.strip().splitlines()[-1]
        found = steps(trace)
        refund = next(
            (s for s in found if s["kind"] == "tool" and s["name"] == "issue_refund"), None
        )
        # The LLM step that asked for the refund: the last one before it.
        before = [
            s for s in found if refund and s["kind"] == "llm" and s["index"] < refund["index"]
        ]
        asked = before[-1] if before else None
        tools_before = asked and [
            s for s in found if s["kind"] == "tool" and s["index"] < asked["index"]
        ]
        (out / f"fork-original-{attempt}.json").write_text(json.dumps(found, indent=1, default=str))
        if asked and tools_before:
            called = [s["name"] for s in found if s["kind"] == "tool"]
            print(f"original {trace}: {called}, issue_refund asked for at step {asked['index']}")
            return trace, asked
        print(f"attempt {attempt}: trace {trace} has no tool call before the refund turn; again")
    raise SystemExit("FORK FAIL: the broken prompt never recorded a refund after a tool call")


def main() -> int:
    out = Path(sys.argv[1])
    log = out / "fork-tool-log.txt"
    log.unlink(missing_ok=True)
    trace, fork = record(out)
    env = {**os.environ, "FORK_PROMPT": "fixed", "FORK_TOOL_LOG": str(log), "PYTHONPATH": str(BIN)}
    cmd = ["trayce", "replay", trace, "--entry", "fork_entry:entry"]
    cmd += ["--from", fork["span_id"], "--samples", "2"]
    done = subprocess.run(cmd, env=env, capture_output=True, text=True)
    (out / "fork-replay.out").write_text(
        f"$ {' '.join(cmd)}\nexit: {done.returncode}\n{done.stderr}{done.stdout}"
    )
    print(done.stderr + done.stdout)
    replay_id = next(
        (line.split()[1] for line in done.stdout.splitlines() if line.startswith("replay ")), None
    )
    if replay_id is None:  # it stopped before creating the replay; its output says why
        print("FAIL the CLI recorded no replay\nFORK FAIL")
        return 1
    detail = api(f"/v1/replays/{replay_id}")
    (out / "fork-replay.json").write_text(json.dumps(detail, indent=1))

    original = steps(trace)
    prefix_tools = [
        s["name"] for s in original if s["kind"] == "tool" and s["index"] < fork["index"]
    ]
    ran = log.read_text().split() if log.exists() else []
    # Control: a whole replay with the same log, so an empty log above means the stub answered,
    # not that the log is broken.
    control = out / "fork-control-tool-log.txt"
    control.unlink(missing_ok=True)
    whole = ["trayce", "replay", trace, "--entry", "fork_entry:entry", "--samples", "1"]
    ctl = subprocess.run(whole, env={**env, "FORK_TOOL_LOG": str(control)}, capture_output=True)
    (out / "fork-control.out").write_bytes(ctl.stderr + ctl.stdout)
    logged = control.read_text().split() if control.exists() else []
    checks = [
        ("the CLI exited 0", done.returncode == 0),
        (
            "the replay is a fork at that step",
            (detail.get("fork_of") or {}).get("span_id") == fork["span_id"],
        ),
        (
            "every run finished without error",
            all(r["error"] is None for r in detail["replay_runs"]),
        ),
    ]
    for run in detail["replay_runs"]:
        calls = [c["tool"] for c in run["trajectory"]]
        live = calls[len(prefix_tools) :]
        n = run["run_index"]
        noted = any(w["step"] == 0 and "system" in w["fields"] for w in run["warnings"])
        checks += [
            (
                f"run {n} played back {prefix_tools} first",
                calls[: len(prefix_tools)] == prefix_tools,
            ),
            (
                f"run {n}'s fork step doesn't call issue_refund (live: {live})",
                live[:1] != ["issue_refund"],
            ),
            (f"run {n} noted the changed system prompt at step 0", noted),
            (f"run {n} stayed on the recording", run["diverged_at"] is None),
        ]
    # The log has a line for every tool that really ran; the prefix's must not be among them
    # unless a live step called the same tool again.
    live_calls = [
        c
        for r in detail["replay_runs"]
        for c in [x["tool"] for x in r["trajectory"]][len(prefix_tools) :]
    ]
    checks += [
        ("the control replay exited 0", ctl.returncode == 0),
        (f"a whole replay logs its real tool calls (log: {logged})", "lookup_order" in logged),
        (f"no played-back tool really ran (log: {ran})", sorted(ran) == sorted(live_calls)),
    ]
    for what, ok in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {what}")
    passed = all(ok for _, ok in checks)
    print("FORK OK" if passed else "FORK FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
