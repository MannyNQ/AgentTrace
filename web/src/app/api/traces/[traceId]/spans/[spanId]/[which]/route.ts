import { getTrace, readPayload } from "@/lib/traces";

/**
 * A span's full input or output from object storage (ADR-0009), fetched only when asked for.
 * The URI comes from the span row, scoped to the project, never from the request.
 */
export async function GET(
  _req: Request,
  ctx: RouteContext<"/api/traces/[traceId]/spans/[spanId]/[which]">,
) {
  const { traceId, spanId, which } = await ctx.params;
  if (which !== "input" && which !== "output") {
    return new Response("not found", { status: 404 });
  }
  const span = (await getTrace(traceId))?.spans.find((s) => s.spanId === spanId);
  if (!span) return new Response("span not found", { status: 404 });
  try {
    return new Response(await readPayload(span, which), {
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  } catch (e) {
    return new Response(e instanceof Error ? e.message : String(e), { status: 500 });
  }
}
