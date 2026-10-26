import Link from "next/link";
import type { Comparison, ScorerComparison } from "@/lib/experiments";
import { formatCost, formatDuration } from "@/lib/format";
import { experimentLabel, pct } from "./ui";

// The comparison with a baseline (ADR-0024, ADR-0025), as the experiment page's panel and the
// compare page show it. The numbers come from ingest; this only lays them out.

export const BASELINE_REASON: Record<NonNullable<Comparison["baseline_reason"]>, string> = {
  merge_base: "at the merge-base",
  base_branch: "newest on the base branch",
  chosen: "chosen here",
};

export function pts(change: number | null, signed = true): string {
  if (change === null) return "—";
  const n = Math.round(change * 100);
  return `${signed && n > 0 ? "+" : ""}${n} pts`;
}

type Status = "regressed" | "underpowered" | "ok" | "not compared";

export function scorerStatus(s: ScorerComparison): Status {
  if (s.items === 0) return "not compared";
  if (s.regressed) return "regressed";
  return s.underpowered ? "underpowered" : "ok";
}

const STATUS_TONE: Record<Status, string> = {
  regressed: "text-bad",
  underpowered: "text-muted",
  ok: "text-good",
  "not compared": "text-muted",
};

export function Verdict({ c }: { c: Comparison }) {
  if (!c.baseline) return <span className="text-muted">no baseline</span>;
  const n = c.regressions;
  return (
    <span className={`font-medium ${n ? "text-bad" : "text-good"}`} data-regressions={n}>
      {n ? `${n} regression${n === 1 ? "" : "s"}` : "no regressions"}
    </span>
  );
}

export function ScorerComparisonTable({ c }: { c: Comparison }) {
  return (
    <div className="mt-3 overflow-x-auto rounded-md border border-line">
      <table className="w-full text-left text-sm" aria-label="Scorer comparison">
        <thead className="border-b border-line text-xs text-muted">
          <tr>
            <th className="px-3 py-2 font-medium">Scorer</th>
            <th className="px-3 py-2 text-right font-medium">Baseline</th>
            <th className="px-3 py-2 text-right font-medium">This run</th>
            <th className="px-3 py-2 text-right font-medium">Change (95% CI)</th>
            <th className="px-3 py-2 text-right font-medium">max_drop</th>
            <th className="px-3 py-2 text-right font-medium">Items</th>
            <th className="px-3 py-2 font-medium">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {c.scorers.map((s) => {
            const status = scorerStatus(s);
            return (
              <tr key={s.scorer} data-scorer={s.scorer} data-status={status}>
                <td className="px-3 py-2 font-mono text-xs font-medium">{s.scorer}</td>
                <td className="px-3 py-2 text-right font-mono text-xs">{pct(s.baseline_rate)}</td>
                <td className="px-3 py-2 text-right font-mono text-xs">{pct(s.current_rate)}</td>
                <td className="px-3 py-2 text-right font-mono text-xs" data-change={s.change ?? ""}>
                  {pts(s.change)}
                  {s.ci_low !== null && (
                    <span className="text-muted">
                      {" "}
                      ({pts(s.ci_low)} to {pts(s.ci_high)})
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs text-muted">{pts(s.max_drop, false)}</td>
                <td className="px-3 py-2 text-right font-mono text-xs text-muted">{s.items}</td>
                <td className={`px-3 py-2 font-mono text-xs ${STATUS_TONE[status]}`}>{status}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function CompareNotes({ c }: { c: Comparison }) {
  const base = c.baseline_stats;
  const cur = c.current_stats;
  const ms = (v: number | null | undefined) => (v == null ? "—" : formatDuration(v));
  return (
    <div className="mt-2 space-y-1 text-xs text-muted">
      {c.scorers.some((s) => s.underpowered) && (
        <p data-underpowered>
          Underpowered: the interval is wider than <span className="font-mono">max_drop</span>, so a drop
          that size could go undetected. More items or samples narrow it.
        </p>
      )}
      <p>
        Cost per sample {formatCost(base?.cost_per_sample ?? null)} → {formatCost(cur.cost_per_sample)} · p50
        latency {ms(base?.p50_latency_ms)} → {ms(cur.p50_latency_ms)} (not gated)
      </p>
    </div>
  );
}

export function BaselineLine({ c }: { c: Comparison }) {
  const b = c.baseline;
  if (!b || !c.baseline_reason) return null;
  return (
    <>
      vs{" "}
      <Link href={`/experiments/${b.id}`} className="font-mono underline" data-baseline={b.id}>
        {experimentLabel(b)}
      </Link>{" "}
      <span className="text-muted">
        ({BASELINE_REASON[c.baseline_reason]}
        {b.git_commit && ` · ${b.git_commit.slice(0, 7)}`})
      </span>
    </>
  );
}
