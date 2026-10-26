import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { connection } from "next/server";
import { getDataset, getVersion } from "@/lib/datasets";
import { getComparison, getExperiment, type ItemSummary } from "@/lib/experiments";
import { Crumbs, count, oneLine } from "../../../../datasets/ui";
import { BaselineLine, CompareNotes, ScorerComparisonTable, Verdict } from "../../../compare";
import { PassCount, experimentLabel } from "../../../ui";

type Props = PageProps<"/experiments/[id]/compare/[baselineId]">;

export default function ComparePage(props: Props) {
  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-10 sm:px-6">
      <Suspense fallback={<p className="text-sm text-muted">Comparing…</p>}>
        <CompareView params={props.params} />
      </Suspense>
    </main>
  );
}

async function CompareView({ params }: Pick<Props, "params">) {
  await connection();
  const { id, baselineId } = await params;
  // The auto-selected comparison first: when it's this baseline (a link from the PR comment), it
  // says why it was chosen. Only otherwise is the pair compared as chosen.
  const [e, b, auto] = await Promise.all([getExperiment(id), getExperiment(baselineId), getComparison(id)]);
  if (!e || !b) notFound();
  const r = auto.ok && auto.data.baseline?.id === baselineId ? auto : await getComparison(id, baselineId);
  const crumbs = <Crumbs trail={[["Experiments", "/experiments"], [experimentLabel(e), `/experiments/${e.id}`]]} />;
  if (!r.ok) {
    return (
      <>
        {crumbs}
        <h1 className="mt-2 text-xl font-semibold tracking-tight">Comparison</h1>
        <p className="mt-3 text-sm text-bad" data-compare-error>
          {r.error.error}
        </p>
      </>
    );
  }
  const c = r.data;
  const [dataset, version, baseVersion] = await Promise.all([
    getDataset(e.dataset_id),
    getVersion(e.dataset_id, e.dataset_version),
    getVersion(b.dataset_id, b.dataset_version),
  ]);
  const inputs = new Map(
    [...(baseVersion?.items ?? []), ...(version?.items ?? [])].map((i) => [i.item_id, i.contents.input]),
  );
  const baseItems = new Map(b.items.map((i) => [i.item_id, i]));
  const regressed = new Map<string, Set<string>>();
  for (const x of c.item_regressions) {
    regressed.set(x.item_id, (regressed.get(x.item_id) ?? new Set()).add(x.scorer));
  }
  // Shared items, regressions first, then in dataset order.
  const shared = e.items
    .filter((i) => baseItems.has(i.item_id))
    .map((item, n) => ({ item, n }))
    .sort((x, y) => Number(regressed.has(y.item.item_id)) - Number(regressed.has(x.item.item_id)) || x.n - y.n)
    .map((x) => x.item);

  return (
    <>
      {crumbs}
      <div className="mt-2 flex flex-wrap items-baseline gap-3">
        <h1 className="text-xl font-semibold tracking-tight">
          {experimentLabel(e)} <span className="text-muted">vs</span> {experimentLabel(b)}
        </h1>
        <Verdict c={c} />
      </div>
      <p className="mt-1 text-sm text-muted">
        <span className="font-mono">
          {dataset?.name ?? e.dataset_id.slice(0, 8)}{" "}
          {b.dataset_version === e.dataset_version
            ? `v${e.dataset_version}`
            : `v${b.dataset_version} → v${e.dataset_version}`}
        </span>{" "}
        · <BaselineLine c={c} />
      </p>

      <section className="mt-8" aria-label="Scorers">
        <h2 className="font-medium">Scorers</h2>
        <p className="mt-1 text-xs text-muted">
          Pass rate over the items both runs scored. A scorer regresses when the whole 95% interval of the change is
          below zero and the drop is larger than its max_drop.
        </p>
        <ScorerComparisonTable c={c} />
        <CompareNotes c={c} />
      </section>

      <section className="mt-8" aria-label="Items">
        <h2 className="font-medium">Items</h2>
        <p className="mt-1 text-xs text-muted">
          Samples passed per scorer, baseline → this run. Item regressions (most samples passed before, most fail now)
          come first; they&apos;re listed, not gated.
        </p>
        <div className="mt-3 overflow-x-auto rounded-md border border-line">
          <table className="w-full text-left text-sm" aria-label="Compared items">
            <thead className="border-b border-line text-xs text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">Input</th>
                {e.scorers.map((s) => (
                  <th key={s.name} className="px-3 py-2 text-right font-mono font-medium">
                    {s.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {shared.map((item) => (
                <ItemRow
                  key={item.item_id}
                  item={item}
                  base={baseItems.get(item.item_id)!}
                  scorers={e.scorers.map((s) => s.name)}
                  regressed={regressed.get(item.item_id)}
                  href={`/experiments/${e.id}/compare/${b.id}/items/${item.item_id}`}
                  input={inputs.get(item.item_id)}
                />
              ))}
            </tbody>
          </table>
        </div>
        {(c.added_items.length > 0 || c.removed_items.length > 0) && (
          <div className="mt-4 grid gap-4 text-sm sm:grid-cols-2" data-not-compared>
            <ItemList title={`Added since the baseline (${count(c.added_items.length, "item")})`} ids={c.added_items} inputs={inputs} />
            <ItemList title={`Removed since the baseline (${count(c.removed_items.length, "item")})`} ids={c.removed_items} inputs={inputs} />
          </div>
        )}
      </section>
    </>
  );
}

function ItemRow({
  item,
  base,
  scorers,
  regressed,
  href,
  input,
}: {
  item: ItemSummary;
  base: ItemSummary;
  scorers: string[];
  regressed: Set<string> | undefined;
  href: string;
  input: unknown;
}) {
  return (
    <tr data-item-id={item.item_id} data-item-regression={regressed ? [...regressed].join(",") : undefined}>
      <td className="max-w-md px-3 py-2">
        <Link href={href} className="block truncate font-mono text-xs hover:underline">
          {oneLine(input) || item.item_id}
        </Link>
      </td>
      {scorers.map((name) => {
        const was = base.scores[name];
        const now = item.scores[name];
        return (
          <td
            key={name}
            className={`px-3 py-2 text-right text-xs ${regressed?.has(name) ? "bg-bad/10" : ""}`}
            data-scorer={name}
          >
            {was || now ? (
              <>
                {was ? <PassCount passed={was.passed} scored={was.scored} /> : <span className="text-muted">n/a</span>}
                <span className="text-muted"> → </span>
                {now ? <PassCount passed={now.passed} scored={now.scored} /> : <span className="text-muted">n/a</span>}
              </>
            ) : (
              <span className="text-muted">n/a</span>
            )}
          </td>
        );
      })}
    </tr>
  );
}

function ItemList({ title, ids, inputs }: { title: string; ids: string[]; inputs: Map<string, unknown> }) {
  if (ids.length === 0) return null;
  return (
    <div>
      <h3 className="text-xs font-medium text-muted">{title}</h3>
      <ul className="mt-1 space-y-1 font-mono text-xs">
        {ids.map((i) => (
          <li key={i} className="truncate">
            {oneLine(inputs.get(i)) || i}
          </li>
        ))}
      </ul>
    </div>
  );
}
