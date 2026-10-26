import { notFound } from "next/navigation";
import { Suspense } from "react";
import { connection } from "next/server";
import { comparingScorer, getExperiment, getExperimentItem } from "@/lib/experiments";
import { liveTraces } from "@/lib/traces";
import { Crumbs, SourceTrace } from "../../../../datasets/ui";
import { Call, Json, SampleCard } from "../../../samples";
import { experimentLabel } from "../../../ui";

type Props = PageProps<"/experiments/[id]/items/[itemId]">;

export default function ExperimentItemPage(props: Props) {
  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-10 sm:px-6">
      <Suspense fallback={<p className="text-sm text-muted">Loading item…</p>}>
        <ItemView params={props.params} />
      </Suspense>
    </main>
  );
}

async function ItemView({ params }: Pick<Props, "params">) {
  await connection();
  const { id, itemId } = await params;
  const [e, detail] = await Promise.all([getExperiment(id), getExperimentItem(id, itemId)]);
  if (!e || !detail) notFound();
  const { item, samples } = detail;
  const c = item.contents;
  const traceIds = [...samples.flatMap((s) => (s.trace_id ? [s.trace_id] : [])), ...(item.source_trace_id ? [item.source_trace_id] : [])];
  const live = await liveTraces(traceIds);
  const comparing = c.expected_trajectory ? comparingScorer(e.scorers) : null;
  const thresholds = new Map(e.scorers.map((s) => [s.name, s.threshold]));

  return (
    <>
      <Crumbs trail={[["Experiments", "/experiments"], [experimentLabel(e), `/experiments/${e.id}`]]} />
      <h1 className="mt-2 text-xl font-semibold tracking-tight">Item</h1>
      <p className="mt-1 font-mono text-xs text-muted">
        {item.item_id} · rev {item.rev} · source <SourceTrace item={item} live={live} />
      </p>

      <section className="mt-6 grid gap-4 md:grid-cols-2" aria-label="Test case">
        <div>
          <h2 className="text-sm font-medium">Input</h2>
          <Json value={c.input} label="Input" />
        </div>
        <div className="space-y-3">
          <div>
            <h2 className="text-sm font-medium">Expected output</h2>
            {c.expected_output === null ? (
              <p className="mt-1 text-xs text-muted">none: output scorers skip this item</p>
            ) : (
              <Json value={c.expected_output} label="Expected output" />
            )}
          </div>
          <div>
            <h2 className="text-sm font-medium">Expected trajectory</h2>
            {c.expected_trajectory ? (
              <ol className="mt-1 space-y-1 font-mono text-xs">
                {c.expected_trajectory.map((t, i) => (
                  <li key={i}>
                    {i + 1}. <Call call={t} />
                  </li>
                ))}
              </ol>
            ) : (
              <p className="mt-1 text-xs text-muted">none: trajectory comparisons skip this item</p>
            )}
          </div>
        </div>
      </section>

      <section className="mt-8" aria-label="Samples">
        <h2 className="font-medium">Samples</h2>
        {comparing && (
          <p className="mt-1 text-xs text-muted">
            Tool calls are lined up the way <span className="font-mono">{comparing.name}</span> compares them (
            {comparing.mode.replace("_", " ")}); the first place they part is marked.
          </p>
        )}
        {samples.length === 0 ? (
          <p className="mt-3 text-sm text-muted">No samples uploaded for this item yet.</p>
        ) : (
          <ul className="mt-3 space-y-4">
            {samples.map((s) => (
              <SampleCard
                key={s.sample_index}
                sample={s}
                expected={c.expected_trajectory}
                mode={comparing?.mode ?? null}
                thresholds={thresholds}
                live={live}
              />
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

