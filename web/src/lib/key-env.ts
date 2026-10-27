/**
 * The environment a new API key goes into, shown once beside the key (design.md, Phase 7). An
 * `ingest` key also gets the OpenTelemetry lines, for agents that send OTLP (ADR-0035); a `full`
 * key is for the CLI and CI, which use the SDK's.
 */
export function keyEnvLines(key: string, scope: "ingest" | "full", endpoint: string): string {
  const base = endpoint.replace(/\/+$/, "");
  const lines = ["# The agenttrace SDK and CLI", `AGENTTRACE_API_KEY=${key}`, `AGENTTRACE_ENDPOINT=${base}`];
  if (scope === "ingest") {
    lines.push(
      "",
      "# Or any OpenTelemetry exporter (OTLP/HTTP)",
      `OTEL_EXPORTER_OTLP_ENDPOINT=${base}/otlp`,
      // Header values are percent-encoded, so the space after Bearer is %20.
      `OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer%20${encodeURIComponent(key)}`,
      "# Records tool arguments, which trajectory scorers match on",
      "OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT=true",
    );
  }
  return lines.join("\n");
}
