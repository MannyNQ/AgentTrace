import { notFound } from "next/navigation";
import { Suspense } from "react";
import { connection } from "next/server";
import { comparingScorer, getExperiment, getExperimentItem, type ExperimentDetail, type ItemDetail } from "@/lib/experiments";
import { liveTraces } from "@/lib/traces";
import { Crumbs } from "../../../../../../datasets/ui";
import { Json, SampleCard } from "../../../../../samples";
import { experimentLabel } from "../../../../../ui";

type Props = PageProps<"/experiments/[id]/compare/[baselineId]/items/[itemId]">;

export default function CompareItemPage(props: Props) {
  return (
    <main className="mx-auto w-full max-w-7xl px-4 py-10 sm:px-6">
      <Suspense fallback={<p className="text-sm text-muted">Loading item…</p>}>
        <CompareItemView params={props.params} />
      </Suspense>
    </main>
  );
}

/** One item's samples in the baseline and in this run, side by side. */
async function CompareItemView({ params }: Pick<Props, "params">) {
  await connection();
  const { id, baselineId, itemId } = await params;
  const [e, b, cur, base] = await Promise.all([
    getExperiment(id),
    getExperiment(baselineId),
    getExperimentItem(id, itemId),
    getExperimentItem(baselineId, itemId),
  ]);
  if (!e || !b || (!cur && !base)) notFound();
  const item = (cur ?? base)!.item;
  const traceIds = [...(cur?.samples ?? []), ...(base?.samples ?? [])].flatMap((s) => (s.trace_id ? [s.trace_id] : []));
  const live = await liveTraces(traceIds);

  return (
    <>
      <Crumbs
        trail={[
          ["Experiments", "/experiments"],
          [experimentLabel(e), `/experiments/${e.id}`],
          [`vs ${experimentLabel(b)}`, `/experiments/${e.id}/compare/${b.id}`],
        ]}
      />
      <h1 className="mt-2 text-xl font-semibold tracking-tight">Item, side by side</h1>
      <p className="mt-1 font-mono text-xs text-muted">{item.item_id}</p>
      <section className="mt-4" aria-label="Input">
        <h2 className="text-sm font-medium">Input</h2>
        <Json value={item.contents.input} label="Input" />
      </section>
      <div className="mt-8 grid gap-6 lg:grid-cols-2">
        <Side title="Baseline" e={b} detail={base} live={live} />
        <Side title="This run" e={e} detail={cur} live={live} />
      </div>
    </>
  );
}

function Side({
  title,
  e,
  detail,
  live,
}: {
  title: string;
  e: ExperimentDetail;
  detail: ItemDetail | null;
  live: Set<string> | null;
}) {
  const thresholds = new Map(e.scorers.map((s) => [s.name, s.threshold]));
  const expected = detail?.item.contents.expected_trajectory ?? null;
  const comparing = expected ? comparingScorer(e.scorers) : null;
  return (
    <section aria-label={title} data-side={title}>
      <h2 className="font-medium">
        {title} <span className="font-mono text-xs text-muted">{experimentLabel(e)}</span>
      </h2>
      {!detail ? (
        <p className="mt-2 text-sm text-muted">This item isn&apos;t in this run&apos;s dataset version.</p>
      ) : detail.samples.length === 0 ? (
        <p className="mt-2 text-sm text-muted">No samples uploaded for this item.</p>
      ) : (
        <ul className="mt-3 space-y-4">
          {detail.samples.map((s) => (
            <SampleCard
              key={s.sample_index}
              sample={s}
              expected={expected}
              mode={comparing?.mode ?? null}
              thresholds={thresholds}
              live={live}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
