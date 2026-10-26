import "server-only";
import { ingest } from "./ingest";

export type { ApiError, Result } from "./ingest";

// ADR-0019: datasets are read and written only through the ingest API, which holds the rules.
const { call, read } = ingest("/v1/datasets");

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
