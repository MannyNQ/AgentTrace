import "server-only";
import { readFileSync } from "node:fs";
import path from "node:path";

// ADR-0019: datasets are read and written only through the ingest API, which holds the rules.
// The API key stays on the server; Phase 6 replaces it with user sessions.
const INGEST_URL = process.env.INGEST_URL ?? "http://localhost:8000";
const API_KEY = process.env.AGENTTRACE_API_KEY || rootEnv("AGENTTRACE_API_KEY") || "at-local-dev-key";

/**
 * A value from the repo-root .env, which docker-compose.yml reads to register ingest's key.
 * Next only loads web/.env*, so without this a customized key would leave the web app sending
 * the default one. Empty counts as unset, like compose's `${VAR:-default}`.
 */
function rootEnv(key: string): string | undefined {
  let text: string;
  try {
    text = readFileSync(path.join(process.cwd(), "..", ".env"), "utf8");
  } catch {
    return undefined;
  }
  const values = [...text.matchAll(new RegExp(`^\\s*${key}\\s*=(.*)$`, "gm"))];
  const value = values.at(-1)?.[1].trim().replace(/^(["'])(.*)\1$/, "$2");
  return value || undefined;
}

export type ToolCall = { tool: string; args?: Record<string, unknown> };

export type Contents = {
  input: unknown;
  expected_output: unknown;
  expected_trajectory: ToolCall[] | null;
  metadata: Record<string, string>;
};

export type Dataset = {
  id: string;
  name: string;
  description: string;
  archived: boolean;
  created_at: string;
  latest_version: number | null;
  draft_items: number;
};

export type Item = {
  item_id: string;
  rev: number;
  source_trace_id: string | null;
  created_at: string;
  contents: Contents;
};

export type DraftItem = Item & { status: "new" | "changed" | "unchanged"; needs_expectation: boolean };
export type VersionInfo = { version: number; item_count: number; published_at: string };
export type VersionDiff = VersionInfo & {
  items: (Item & { change: "added" | "changed" | "unchanged" })[];
  removed: Item[];
};

/** A refusal from the API: `error` for people, plus whatever else the API said. */
export type ApiError = {
  status: number;
  error: string;
  items?: string[]; // publish: the offending items
  item_id?: string; // from-trace: the item already holding this trace
  dataset_id?: string; // a taken name: the dataset holding it, which may be archived
  rev?: number; // a stale edit: the item's current revision
  fields?: Record<string, string>; // validation errors by top-level field
};

export type Result<T> = { ok: true; data: T } | { ok: false; error: ApiError };

function toError(status: number, detail: unknown): ApiError {
  // The dataset API answers {"error": ..., ...}, with `fields` for validation errors.
  if (detail && typeof detail === "object" && "error" in detail) {
    return { status, ...(detail as { error: string }) };
  }
  return { status, error: typeof detail === "string" ? detail : `ingest API answered ${status}` };
}

async function call<T>(method: string, path: string, body?: unknown): Promise<Result<T>> {
  let res: Response;
  try {
    res = await fetch(`${INGEST_URL}/v1/datasets${path}`, {
      method,
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${API_KEY}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(30_000),
    });
  } catch (e) {
    return { ok: false, error: { status: 0, error: `ingest API unreachable: ${e instanceof Error ? e.message : e}` } };
  }
  if (res.status === 204) return { ok: true, data: undefined as T };
  const json = await res.json().catch(() => ({}));
  return res.ok ? { ok: true, data: json as T } : { ok: false, error: toError(res.status, json.detail) };
}

/** For reads in server components: a failed read is an error to show, not a refusal to handle. */
async function read<T>(path: string): Promise<T | null> {
  const r = await call<T>("GET", path);
  if (r.ok) return r.data;
  if (r.error.status === 404) return null;
  throw new Error(r.error.error);
}

const id = (s: string) => encodeURIComponent(s);

export const listDatasets = async (includeArchived = false) =>
  (await read<{ datasets: Dataset[] }>(includeArchived ? "?include_archived=true" : ""))?.datasets ?? [];
export const getDataset = (dataset: string) => read<Dataset>(`/${id(dataset)}`);
export const getDraft = (dataset: string) =>
  read<{ latest_version: number | null; items: DraftItem[] }>(`/${id(dataset)}/items`);
export const listVersions = async (dataset: string) =>
  (await read<{ versions: VersionInfo[] }>(`/${id(dataset)}/versions`))?.versions ?? [];
export const getVersion = (dataset: string, n: number) =>
  read<VersionDiff>(`/${id(dataset)}/versions/${n}`);

/** With `traceId`, the new draft starts with that trace, or nothing is created. */
export const createDataset = (name: string, description: string, traceId?: string) =>
  call<Dataset>("POST", "", { name, description, ...(traceId ? { trace_id: traceId } : {}) });
export const patchDataset = (
  dataset: string,
  patch: { name?: string; description?: string; archived?: boolean },
) => call<Dataset>("PATCH", `/${id(dataset)}`, patch);
export const addItem = (dataset: string, contents: Partial<Contents>) =>
  call<Item>("POST", `/${id(dataset)}/items`, contents);
export const editItem = (dataset: string, item: string, rev: number, contents: Partial<Contents>) =>
  call<Item>("PATCH", `/${id(dataset)}/items/${id(item)}`, { rev, contents });
export const deleteItem = (dataset: string, item: string, rev: number) =>
  call<void>("DELETE", `/${id(dataset)}/items/${id(item)}?rev=${rev}`);
export const addFromTrace = (dataset: string, traceId: string) =>
  call<Item>("POST", `/${id(dataset)}/items/from-trace`, { trace_id: traceId });
export const publish = (dataset: string) =>
  call<{ version: number }>("POST", `/${id(dataset)}/publish`);
