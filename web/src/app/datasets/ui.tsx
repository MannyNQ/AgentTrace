import Link from "next/link";
import type { Contents, Item } from "@/lib/datasets";

export function Crumbs({ trail }: { trail: [string, string?][] }) {
  return (
    <p className="font-mono text-xs uppercase tracking-widest text-muted">
      {trail.map(([label, href], i) => (
        <span key={label}>
          {i > 0 && " / "}
          {href ? (
            <Link href={href} className="hover:underline">
              {label}
            </Link>
          ) : (
            label
          )}
        </span>
      ))}
    </p>
  );
}

export function count(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

export function oneLine(v: unknown): string {
  if (v === null || v === undefined) return "";
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > 120 ? `${s.slice(0, 120)}…` : s;
}

export function trajectorySummary(c: Contents): string {
  if (!c.expected_trajectory) return "";
  return c.expected_trajectory.map((t) => t.tool).join(" → ") || "(no tool calls)";
}

/** `live` is null when traces can't be checked; the link is then shown unchecked. */
export function SourceTrace({ item, live }: { item: Item; live: Set<string> | null }) {
  if (!item.source_trace_id) return <>—</>;
  if (live && !live.has(item.source_trace_id)) {
    return (
      <span className="text-muted" title={item.source_trace_id} data-source-expired>
        source trace expired
      </span>
    );
  }
  return (
    <Link href={`/traces/${item.source_trace_id}`} className="hover:underline">
      {item.source_trace_id.slice(0, 8)}…
    </Link>
  );
}

/** An item's full contents as JSON, for the version view. */
export function ContentsView({ contents }: { contents: Contents }) {
  return (
    <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-all rounded bg-line/40 p-2 font-mono text-xs">
      {JSON.stringify(contents, null, 2)}
    </pre>
  );
}

const BADGE: Record<string, string> = {
  new: "text-good",
  added: "text-good",
  changed: "text-foreground",
  unchanged: "text-muted",
  removed: "text-bad",
};

export function ChangeBadge({ change }: { change: string }) {
  return (
    <span className={`font-mono text-xs ${BADGE[change] ?? ""}`} data-change={change}>
      {change}
    </span>
  );
}
