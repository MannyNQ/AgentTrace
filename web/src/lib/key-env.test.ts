import { test } from "node:test";
import assert from "node:assert/strict";
import { keyEnvLines } from "./key-env.ts";

test("an ingest key gets the SDK lines and the OTLP lines", () => {
  const lines = keyEnvLines("at-abc", "ingest", "https://traces.example.com/").split("\n");
  assert.ok(lines.includes("AGENTTRACE_API_KEY=at-abc"));
  assert.ok(lines.includes("AGENTTRACE_ENDPOINT=https://traces.example.com"));
  assert.ok(lines.includes("OTEL_EXPORTER_OTLP_ENDPOINT=https://traces.example.com/otlp"));
  assert.ok(lines.includes("OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer%20at-abc"));
  assert.ok(lines.includes("OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT=true"));
});

test("a full key is for the CLI and CI, so it gets only the SDK lines", () => {
  const text = keyEnvLines("at-abc", "full", "http://localhost:8000");
  assert.ok(text.includes("AGENTTRACE_API_KEY=at-abc"));
  assert.ok(!text.includes("OTEL_"));
});

test("a key with characters a header value can't hold is percent-encoded", () => {
  const text = keyEnvLines("a,b=c", "ingest", "http://localhost:8000");
  assert.ok(text.includes("OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer%20a%2Cb%3Dc"));
});
