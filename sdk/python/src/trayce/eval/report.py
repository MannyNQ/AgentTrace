"""The comparison with the baseline, as the CLI prints it and as the PR comment's markdown
(`trayce eval --report-md`). Rendering only: the numbers come from ingest (ADR-0024).

The markdown names items by ID, never by input: dataset items are often copied from production
traces, and a PR comment or job summary can be public. Everything in it is IDs, numbers, and names
the config constrains (scorer names, dataset slugs), so nothing needs escaping."""

from typing import Any

MAX_ITEM_REGRESSIONS = 10
REASONS = {
    "merge_base": "at the merge-base",
    "base_branch": "newest on the base branch",
    "chosen": "chosen by hand",
}


def _pct(rate: float | None) -> str:
    return "—" if rate is None else f"{rate:.0%}"


def _pts(change: float | None, signed: bool = True) -> str:
    if change is None:
        return "—"
    pts = round(change * 100)
    return f"{pts:+d} pts" if signed else f"{pts} pts"


def _ci(s: dict[str, Any]) -> str:
    if s["ci_low"] is None:
        return ""
    return f" ({_pts(s['ci_low'])} to {_pts(s['ci_high'])})"


def status(s: dict[str, Any]) -> str:
    if s["items"] == 0:
        return "not compared"
    if s["regressed"]:
        return "regressed"
    return "underpowered" if s["underpowered"] else "ok"


def verdict(cmp: dict[str, Any]) -> str:
    if cmp["baseline"] is None:
        return "no baseline"
    n = cmp["regressions"]
    return f"{n} regression{'s' if n != 1 else ''}" if n else "no regressions"


def _money(v: float | None) -> str:
    return "—" if v is None else f"${v:.4f}"


def _secs(ms: float | None) -> str:
    return "—" if ms is None else f"{ms / 1000:.1f} s"


def text(cmp: dict[str, Any]) -> str:
    """The comparison for the terminal."""
    b = cmp["baseline"]
    if b is None:
        return (
            "baseline: none yet (no completed run with this eval.yaml at the merge-base or on "
            "the base branch)"
        )
    lines = [
        f"vs baseline {b['id']} ({REASONS[cmp['baseline_reason']]}): {verdict(cmp)}",
    ]
    width = max((len(s["scorer"]) for s in cmp["scorers"]), default=0)
    for s in cmp["scorers"]:
        lines.append(
            f"  {s['scorer']:<{width}}  {_pct(s['baseline_rate']):>4} -> "
            f"{_pct(s['current_rate']):>4}"
            f"  {_pts(s['change'])}{_ci(s)}  max_drop {_pts(s['max_drop'], False)}"
            f"  over {s['items']} items  {status(s)}"
        )
    for r in cmp["item_regressions"]:
        lines.append(
            f"  item regression {str(r['item_id'])[:8]} {r['scorer']}: "
            f"{r['baseline_passed']}/{r['baseline_scored']} -> "
            f"{r['current_passed']}/{r['current_scored']}"
        )
    return "\n".join(lines)


def markdown(
    detail: dict[str, Any],
    cmp: dict[str, Any],
    dataset: str,
    web_url: str | None = None,
) -> str:
    """The PR comment (architecture.md, Phase 4): verdict, scorer table, cost and latency, item
    regressions, and items not compared."""
    web = web_url.rstrip("/") if web_url else None
    e, b = cmp["experiment"], cmp["baseline"]

    def exp_link(x: dict[str, Any], label: str) -> str:
        return f"[{label}]({web}/experiments/{x['id']})" if web else f"{label} `{x['id'][:8]}`"

    icon = "⚪" if b is None else ("❌" if cmp["regressions"] else "✅")
    out = [f"### {icon} Trayce eval: {verdict(cmp)}", ""]
    summary = (
        f"`{dataset}` v{e['dataset_version']} · {e['item_count']} items × {e['samples']} samples"
        f" · {exp_link(e, 'this run')}"
    )
    if b is not None:
        commit = f" `{b['git_commit'][:7]}`" if b.get("git_commit") else ""
        summary += f" vs {exp_link(b, 'baseline')} ({REASONS[cmp['baseline_reason']]}{commit})"
    out += [summary, ""]

    if b is None:
        out += [
            "No completed run with this `eval.yaml` exists at the merge-base or on the base "
            "branch yet, so there's nothing to compare against and the check passes. Scores:",
            "",
            "| Scorer | Pass rate | Passed |",
            "|---|---|---|",
        ]
        for s in detail["scorer_stats"]:
            rate = _pct(s["pass_rate"])
            out.append(f"| `{s['scorer']}` | {rate} | {s['passed']}/{s['scored']} |")
        return "\n".join(out) + "\n"

    marks = {"regressed": "❌ regressed", "ok": "✅ ok", "underpowered": "⚠️ underpowered"}
    out += [
        "| Scorer | Baseline | Current | Change (95% CI) | max_drop | Items | Status |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in cmp["scorers"]:
        out.append(
            f"| `{s['scorer']}` | {_pct(s['baseline_rate'])} | {_pct(s['current_rate'])} "
            f"| {_pts(s['change'])}{_ci(s)} | {_pts(s['max_drop'], False)} | {s['items']} "
            f"| {marks.get(status(s), status(s))} |"
        )
    if any(s["underpowered"] for s in cmp["scorers"]):
        out += [
            "",
            "⚠️ Underpowered: the interval is wider than `max_drop`, so a drop that size could "
            "go undetected. More items or `samples` narrow it.",
        ]
    cur, base = cmp["current_stats"], cmp["baseline_stats"] or {}
    out += [
        "",
        f"Cost per sample {_money(base.get('cost_per_sample'))} → "
        f"{_money(cur['cost_per_sample'])} · p50 latency {_secs(base.get('p50_latency_ms'))} → "
        f"{_secs(cur['p50_latency_ms'])} (not gated)",
    ]

    regressions = cmp["item_regressions"]
    if regressions:
        heading = "**Item regressions** (most samples passed before, most fail now; not gated)"
        out += ["", heading, ""]
        for r in regressions[:MAX_ITEM_REGRESSIONS]:
            label = f"item `{str(r['item_id'])[:8]}`"
            if web:
                href = f"{web}/experiments/{e['id']}/compare/{b['id']}/items/{r['item_id']}"
                label = f"[{label}]({href})"
            out.append(
                f"- {label} · `{r['scorer']}` {r['baseline_passed']}/{r['baseline_scored']} → "
                f"{r['current_passed']}/{r['current_scored']}"
            )
        if len(regressions) > MAX_ITEM_REGRESSIONS:
            out.append(f"- +{len(regressions) - MAX_ITEM_REGRESSIONS} more")

    added, removed = len(cmp["added_items"]), len(cmp["removed_items"])
    if added or removed:
        out += ["", f"Not compared: {added} items added, {removed} removed since the baseline."]
    return "\n".join(out) + "\n"


def failed_markdown(error: str) -> str:
    return f"### ❌ Trayce eval: the run failed\n\n```\n{error}\n```\n"
