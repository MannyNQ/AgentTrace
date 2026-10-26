import Link from "next/link";
import type { ExperimentInfo, ExperimentStatus } from "@/lib/experiments";
import { projectLinks } from "@/lib/links";

const STATUS: Record<ExperimentStatus, string> = {
  running: "text-muted",
  completed: "text-good",
  failed: "text-bad",
};

export function StatusBadge({ status }: { status: ExperimentStatus }) {
  return (
    <span className={`font-mono text-xs ${STATUS[status]}`} data-status={status}>
      {status}
    </span>
  );
}

export function pct(rate: number | null): string {
  return rate === null ? "—" : `${Math.round(rate * 100)}%`;
}

/** `passed/scored`, green when every graded sample passed and red when most failed. */
export function PassCount({ passed, scored }: { passed: number; scored: number }) {
  if (scored === 0) return <span className="text-muted">n/a</span>;
  const tone = passed === scored ? "text-good" : passed * 2 <= scored ? "text-bad" : "";
  return (
    <span className={`font-mono ${tone}`} data-passed={passed} data-scored={scored}>
      {passed}/{scored}
    </span>
  );
}

export function experimentLabel(e: Pick<ExperimentInfo, "id" | "name">): string {
  return e.name ?? e.id.slice(0, 8);
}

export function GitRef({ e }: { e: Pick<ExperimentInfo, "git_branch" | "git_commit"> }) {
  if (!e.git_branch && !e.git_commit) return <span className="text-muted">—</span>;
  return (
    <span className="font-mono text-xs">
      {e.git_branch}
      {e.git_commit && <span className="text-muted"> @ {e.git_commit.slice(0, 7)}</span>}
    </span>
  );
}

/** A sample's trace: `live` is null when ClickHouse can't be checked, so the link goes unchecked.
 * Experiment traces aren't pinned (ADR-0021), so old ones expire. */
export async function SampleTrace({ traceId, live }: { traceId: string | null; live: Set<string> | null }) {
  const at = await projectLinks();
  if (!traceId) return <span className="text-muted">—</span>;
  if (live && !live.has(traceId)) {
    return (
      <span className="text-muted" title={traceId} data-trace-expired>
        trace expired
      </span>
    );
  }
  return (
    <Link href={at(`/traces/${traceId}`)} className="font-mono text-xs underline" data-trace-link={traceId}>
      trace {traceId.slice(0, 8)}…
    </Link>
  );
}
