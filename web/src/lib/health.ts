export type StoreName = "postgres" | "clickhouse" | "redis" | "s3";

export type Health =
  | { reachable: true; status: "ok" | "degraded"; stores: Record<StoreName, string> }
  | { reachable: false; error: string };

const INGEST_URL = process.env.INGEST_URL ?? "http://localhost:8000";

export async function getHealth(): Promise<Health> {
  try {
    const res = await fetch(`${INGEST_URL}/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    // /health answers 503 with a body when a store is down, so read the body either way.
    const body = await res.json();
    return { reachable: true, status: body.status, stores: body.stores };
  } catch (e) {
    return { reachable: false, error: e instanceof Error ? e.message : String(e) };
  }
}
