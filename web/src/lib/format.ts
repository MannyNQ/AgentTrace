export function formatDuration(ms: number): string {
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

/** ADR-0010: unpriced LLM spans make the total a lower bound, so say so. */
export function formatCost(usd: number | null, unpriced = 0): string {
  if (usd === null) return "—";
  const value = usd < 1 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
  return unpriced > 0 ? `≥ ${value}` : value;
}

export function formatTime(d: Date): string {
  return d.toISOString().replace("T", " ").slice(0, 19) + " UTC";
}

/** Pretty-print a JSON preview; previews of offloaded payloads are cut and stay as text. */
export function prettyPayload(preview: string): string {
  try {
    return JSON.stringify(JSON.parse(preview), null, 2);
  } catch {
    return preview;
  }
}
