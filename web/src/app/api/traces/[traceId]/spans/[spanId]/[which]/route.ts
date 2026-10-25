import { GetObjectCommand } from "@aws-sdk/client-s3";
import { getTrace } from "@/lib/traces";
import { S3_BUCKET, getS3 } from "@/lib/stores";

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

  const uri = which === "input" ? span.inputUri : span.outputUri;
  if (!uri) {
    // Small payloads are stored whole as the preview.
    const preview = which === "input" ? span.inputPreview : span.outputPreview;
    return new Response(preview, { headers: { "Content-Type": "application/json" } });
  }
  const prefix = `s3://${S3_BUCKET}/`;
  if (!uri.startsWith(prefix)) return new Response("unexpected payload uri", { status: 500 });

  const object = await getS3().send(
    new GetObjectCommand({ Bucket: S3_BUCKET, Key: uri.slice(prefix.length) }),
  );
  return new Response(await object.Body?.transformToString(), {
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}
