import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { connection } from "next/server";
import { getDataset, getVersion, type Item } from "@/lib/datasets";
import { liveTraces } from "@/lib/traces";
import { formatTime } from "@/lib/format";
import { ChangeBadge, ContentsView, Crumbs, SourceTrace, count, oneLine } from "../../../ui";
import { projectLinks } from "@/lib/links";

type Props = PageProps<"/p/[slug]/datasets/[id]/versions/[version]">;

export default function VersionPage(props: Props) {
  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-10 sm:px-6">
      <Suspense fallback={<p className="text-sm text-muted">Loading…</p>}>
        <VersionView params={props.params} />
      </Suspense>
    </main>
  );
}

async function VersionView({ params }: Pick<Props, "params">) {
  const at = await projectLinks();
  await connection();
  const { id, version } = await params;
  const n = /^\d+$/.test(version) ? Number(version) : NaN;
  const [dataset, diff] = await Promise.all([getDataset(id), Number.isInteger(n) ? getVersion(id, n) : null]);
  if (!dataset || !diff) notFound();
  const all = [...diff.items, ...diff.removed];
  const live = await liveTraces(all.flatMap((i) => (i.source_trace_id ? [i.source_trace_id] : [])));
  const tally = (c: string) => diff.items.filter((i) => i.change === c).length;
  const order = { added: 0, changed: 1, unchanged: 2 };
  const items = [...diff.items].sort((a, b) => order[a.change] - order[b.change]);

  return (
    <>
      <Crumbs trail={[["Datasets", "/datasets"], [dataset.name, `/datasets/${id}`]]} />
      <h1 className="mt-2 font-mono text-2xl font-semibold tracking-tight">
        {dataset.name} v{diff.version}
      </h1>
      <p className="mt-1 text-sm text-muted">
        {count(diff.item_count, "item")} · published {formatTime(new Date(diff.published_at))} · read-only
      </p>
      <p className="mt-3 text-sm" data-diff-summary>
        {n > 1 ? `Versus v${n - 1}: ` : "First version: "}
        {tally("added")} added · {tally("changed")} changed · {diff.removed.length} removed ·{" "}
        {tally("unchanged")} unchanged
      </p>
      {n > 1 && (
        <p className="text-xs text-muted">
          <Link href={at(`/datasets/${id}/versions/${n - 1}`)} className="underline">
            v{n - 1}
          </Link>
        </p>
      )}
      <ul className="mt-4 divide-y divide-line rounded-md border border-line" aria-label="Version items">
        {items.map((item) => (
          <ItemRow key={item.item_id} item={item} change={item.change} live={live} />
        ))}
        {diff.removed.map((item) => (
          <ItemRow key={item.item_id} item={item} change="removed" live={live} />
        ))}
      </ul>
    </>
  );
}

function ItemRow({ item, change, live }: { item: Item; change: string; live: Set<string> | null }) {
  return (
    <li data-item-id={item.item_id} data-change={change}>
      <details className="px-3 py-2">
        <summary className="flex cursor-pointer flex-wrap items-baseline gap-3 text-sm">
          <ChangeBadge change={change} />
          <span className="min-w-0 flex-1 truncate font-mono text-xs">{oneLine(item.contents.input)}</span>
          <span className="font-mono text-xs text-muted">rev {item.rev}</span>
          <span className="font-mono text-xs">
            <SourceTrace item={item} live={live} />
          </span>
        </summary>
        <p className="mt-2 text-xs text-muted">
          {change === "removed" ? "Contents as of the previous version:" : "Contents as of this version:"}
        </p>
        <ContentsView contents={item.contents} />
      </details>
    </li>
  );
}
