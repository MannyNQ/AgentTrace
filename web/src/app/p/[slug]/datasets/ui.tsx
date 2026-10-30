import Link from "next/link";
import { Suspense } from "react";
import { projectLinks } from "@/lib/links";
import type { Contents, Item } from "@/lib/datasets";

/** "Trayce / <project> / ...". `trail` paths are inside the project, like "/datasets". The
 * links need the request's project, so the static shell shows the labels until they stream in. */
export function Crumbs({ trail }: { trail: [string, string?][] }) {
  return (
    <p className="font-mono text-xs uppercase tracking-widest text-muted">
      <Suspense fallback={trail.map(([label]) => label).join(" / ")}>
        <LinkedCrumbs trail={trail} />
      </Suspense>
    </p>
  );
}

async function LinkedCrumbs({ trail }: { trail: [string, string?][] }) {
  const at = await projectLinks();
  const slug = at("").slice("/p/".length);
  const all: [string, string?][] = [["Trayce", "/"], [slug, at("")], ...trail.map(([l, h]): [string, string?] => [l, h && at(h)])];
  return all.map(([label, href], i) => (
    <span key={`${i}-${label}`}>
      {i > 0 && " / "}
      {href ? (
        <Link href={href} className="hover:underline">
          {label}
        </Link>
      ) : (
        label
      )}
    </span>
  ));
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
export async function SourceTrace({ item, live }: { item: Item; live: Set<string> | null }) {
  const at = await projectLinks();
  if (!item.source_trace_id) return <>—</>;
  if (live && !live.has(item.source_trace_id)) {
    return (
      <span className="text-muted" title={item.source_trace_id} data-source-expired>
        source trace expired
      </span>
    );
  }
  return (
    <Link href={at(`/traces/${item.source_trace_id}`)} className="hover:underline">
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
