import Link from "next/link";
import type { ReplayInfo } from "@/lib/replays";
import { formatTime } from "@/lib/format";
import { PassCount, StatusBadge } from "../experiments/ui";
import { projectLinks } from "@/lib/links";

// Replays (ADR-0028, ADR-0029) as the trace page lists them and the replay page heads them.

/** "at abc1234 + uncommitted changes": the code a replay ran (GLOSSARY.md "Replay"). */
export function ReplayCode({ r }: { r: Pick<ReplayInfo, "git_branch" | "git_commit" | "git_dirty"> }) {
  if (!r.git_commit && !r.git_branch) return <span className="text-muted">unknown code</span>;
  return (
    <span className="font-mono text-xs" data-dirty={r.git_dirty ?? undefined}>
      {r.git_branch}
      {r.git_commit && <span className="text-muted"> @ {r.git_commit.slice(0, 7)}</span>}
      {r.git_dirty && <span className="text-bad"> + uncommitted changes</span>}
    </span>
  );
}

export function PassCounts({ r }: { r: ReplayInfo }) {
  if (!r.item_id) return <span className="text-xs text-muted">unscored</span>;
  if (r.pass_counts.length === 0) return <span className="text-xs text-muted">no scores yet</span>;
  return (
    <span className="flex flex-wrap gap-x-3 text-xs">
      {r.pass_counts.map((c) => (
        <span key={c.scorer} data-pass-count={c.scorer}>
          <span className="font-mono text-muted">{c.scorer}</span> <PassCount passed={c.passed} scored={c.scored} />
        </span>
      ))}
    </span>
  );
}

/** The trace page's list of this trace's replays, newest first. */
export async function ReplayList({ replays }: { replays: ReplayInfo[] }) {
  const at = await projectLinks();
  return (
    <ul className="mt-2 divide-y divide-line rounded-md border border-line text-sm">
      {replays.map((r) => (
        <li key={r.id} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 px-3 py-2" data-replay={r.id}>
          <Link href={at(`/replays/${r.id}`)} className="font-mono text-xs underline">
            {formatTime(new Date(r.started_at))}
          </Link>
          <StatusBadge status={r.status} />
          <ReplayCode r={r} />
          <ReplayKind r={r} />
          <span className="font-mono text-xs text-muted">
            {r.runs_uploaded}/{r.runs} runs
          </span>
          <span className="ml-auto">
            <PassCounts r={r} />
          </span>
        </li>
      ))}
    </ul>
  );
}

/** "fork at span ab12cd34" and "tools stubbed" for a replay that isn't a plain whole re-run. */
export function ReplayKind({ r }: { r: Pick<ReplayInfo, "fork_of" | "tool_mode"> }) {
  if (!r.fork_of && r.tool_mode === "live") return null;
  return (
    <span className="flex gap-2 font-mono text-xs text-muted">
      {r.fork_of && (
        <span data-fork-of={r.fork_of.span_id}>
          fork at span {r.fork_of.span_id.slice(0, 8)}
        </span>
      )}
      {r.tool_mode === "recorded" && <span data-tools-stubbed>tools stubbed</span>}
    </span>
  );
}

/**
 * "Fork from here" on an LLM or tool span (ADR-0040): the command, since the server can't run
 * user code (ADR-0006), or why this span can't be a fork point.
 */
export function ForkCommand({ traceId, spanId, refused }: { traceId: string; spanId: string; refused: string | null }) {
  return (
    <details className="mt-4 text-sm" data-fork-command data-fork-refused={refused ? true : undefined}>
      <summary className="cursor-pointer select-none font-medium">Fork from here</summary>
      {refused ? (
        <p className="mt-2 text-xs">
          <span className="text-bad">This span can&apos;t be a fork point:</span> <span className="text-muted">{refused}</span>
        </p>
      ) : (
        <>
          <p className="mt-2 text-xs text-muted">
            Plays the trace back up to this step, answering each earlier LLM and tool call from the recording, then
            runs your current code live from here. Add <span className="font-mono">--stub-tools</span> to answer
            later tool calls from the recording too.
          </p>
          <pre className="mt-2 select-all overflow-x-auto rounded bg-line/40 p-2 font-mono text-xs">
            trayce replay {traceId} --from {spanId}
          </pre>
        </>
      )}
    </details>
  );
}

/** The server can't run user code (ADR-0006), so "Replay" shows the command to run locally. */
export function ReplayCommand({ traceId }: { traceId: string }) {
  return (
    <details className="text-sm" data-replay-command>
      <summary className="cursor-pointer select-none font-medium">Replay</summary>
      <p className="mt-2 text-xs text-muted">
        Run this where your agent&apos;s code and <span className="font-mono">eval.yaml</span> live, with{" "}
        <span className="font-mono">TRAYCE_API_KEY</span> set. It re-runs the trace&apos;s input with your
        current code and, if the trace is in your dataset&apos;s draft with an expectation, scores each run.
      </p>
      <pre className="mt-2 select-all overflow-x-auto rounded bg-line/40 p-2 font-mono text-xs">
        trayce replay {traceId}
      </pre>
    </details>
  );
}
