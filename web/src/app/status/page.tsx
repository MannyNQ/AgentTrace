import { Suspense } from "react";
import { connection } from "next/server";
import { getHealth, type StoreName } from "@/lib/health";

const STORE_LABELS: Record<StoreName, string> = {
  postgres: "Postgres · orgs, projects, datasets",
  clickhouse: "ClickHouse · spans",
  redis: "Redis · ingest queue",
  s3: "Object storage · large payloads",
};

/** Public: it shows only what ingest's unauthenticated /health shows (ADR-0031). */
export default function StatusPage() {
  return (
    <main className="mx-auto w-full max-w-2xl px-4 py-16 sm:px-6">
      <p className="font-mono text-xs uppercase tracking-widest text-muted">Trayce</p>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">Status</h1>
      <section className="mt-6">
        <h2 className="text-sm font-medium">Service health</h2>
        <Suspense fallback={<p className="mt-3 text-sm text-muted">Checking services…</p>}>
          <ServiceHealth />
        </Suspense>
      </section>
    </main>
  );
}

async function ServiceHealth() {
  await connection(); // health is per-request, never prerendered
  const health = await getHealth();

  if (!health.reachable) {
    return (
      <p className="mt-3 rounded-md border border-line p-4 text-sm">
        <span className="font-medium text-bad">Ingest API unreachable.</span>{" "}
        <span className="text-muted">
          Run <code className="font-mono">docker compose up -d</code> ({health.error})
        </span>
      </p>
    );
  }

  return (
    <ul className="mt-3 divide-y divide-line rounded-md border border-line">
      {(Object.keys(STORE_LABELS) as StoreName[]).map((name) => {
        const result = health.stores[name] ?? "error: not reported";
        const ok = result === "ok";
        return (
          <li key={name} className="flex items-start gap-3 p-4 text-sm">
            <span
              aria-hidden
              className={`mt-1.5 size-2 shrink-0 rounded-full ${ok ? "bg-good" : "bg-bad"}`}
            />
            <div className="min-w-0">
              <p>{STORE_LABELS[name]}</p>
              <p className={`break-words font-mono text-xs ${ok ? "text-muted" : "text-bad"}`}>
                {ok ? "ok" : result}
              </p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
