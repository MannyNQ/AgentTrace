"""POST /otlp/v1/traces: OTLP/HTTP spans, translated into spans and queued like `/v1/spans`
(ADR-0035).

Unlike `/v1/spans`, the senders are third-party instrumentation, so a span that can't be stored
is dropped and reported in `partial_success` rather than failing the export. Errors follow
OTLP's retry rules: exporters retry 503 but not 400 or 500.
"""

import base64
import json
import logging
import zlib
from collections.abc import Iterable
from typing import Annotated, Any, Literal
from uuid import UUID

import redis.exceptions
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from google.protobuf import json_format
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span as ProtoSpan
from opentelemetry.proto.trace.v1.trace_pb2 import Status

from trayce.otel import OtelEvent, OtelSpan, Unstorable, translate
from trayce_ingest.auth import Auth, ProjectAccess
from trayce_ingest.settings import Settings
from trayce_ingest.spans import SpanQueue

log = logging.getLogger(__name__)

PROTOBUF = "application/x-protobuf"
JSON = "application/json"
RETRY_AFTER_S = 5
_HAS_IS_REMOTE = 0x100  # SPAN_FLAGS_CONTEXT_HAS_IS_REMOTE_MASK
_IS_REMOTE = 0x200  # SPAN_FLAGS_CONTEXT_IS_REMOTE_MASK
_STATUS: dict[int, Literal["ok", "error"]] = {
    Status.STATUS_CODE_OK: "ok",
    Status.STATUS_CODE_ERROR: "error",
}


def router(s: Settings, a: Auth, queue: SpanQueue) -> APIRouter:
    r = APIRouter()
    project = ProjectAccess(a, scope="ingest")

    @r.post("/otlp/v1/traces")
    async def export_traces(
        request: Request, project_id: Annotated[UUID, Depends(project)]
    ) -> Response:
        media = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if media not in (PROTOBUF, JSON):
            raise HTTPException(415, f"send {PROTOBUF} or {JSON}")
        body = await request.body()  # size-limited by BodyLimit (app.py), before decompression
        if request.headers.get("content-encoding", "").lower() == "gzip":
            body = _gunzip(body, s.max_request_bytes)
        export = _decode(body, media)

        decoded = [o for rs in export.resource_spans for o in _spans(rs)]
        if len(decoded) > s.max_spans_per_request:
            raise HTTPException(400, f"more than {s.max_spans_per_request} spans in one request")
        spans, reasons = [], []
        for o in decoded:
            try:
                spans.append(translate(o))
            except Unstorable as e:
                reasons.append(f"span {o.span_id or '?'}: {e}")
            except Exception as e:  # one span the translator can't handle mustn't cost the rest
                log.exception("can't translate OTLP span %s", o.span_id)
                reasons.append(
                    f"span {o.span_id or '?'}: couldn't be translated ({type(e).__name__})"
                )
        if spans:
            spans_json = json.dumps([sp.model_dump(mode="json") for sp in spans])
            try:
                await queue.push(project_id, spans_json)
            except redis.exceptions.RedisError as e:
                raise HTTPException(
                    503, f"can't queue spans: {e}", headers={"Retry-After": str(RETRY_AFTER_S)}
                ) from e

        resp = ExportTraceServiceResponse()
        if reasons:
            resp.partial_success.rejected_spans = len(reasons)
            more = f" (and {len(reasons) - 1} more)" if len(reasons) > 1 else ""
            resp.partial_success.error_message = reasons[0] + more
        if media == PROTOBUF:
            return Response(resp.SerializeToString(), media_type=PROTOBUF)
        return Response(json_format.MessageToJson(resp, indent=None), media_type=JSON)

    return r


def _gunzip(body: bytes, max_bytes: int) -> bytes:
    """Decompressed, refusing anything over the request limit before it's all in memory."""
    d = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    try:
        out = d.decompress(body, max_bytes + 1)
    except zlib.error as e:
        raise HTTPException(400, f"body isn't valid gzip: {e}") from e
    if len(out) > max_bytes:
        raise HTTPException(400, f"decompressed body over {max_bytes} bytes")
    if not d.eof:
        raise HTTPException(400, "body isn't valid gzip: truncated")
    return out


def _decode(body: bytes, media: str) -> ExportTraceServiceRequest:
    export = ExportTraceServiceRequest()
    try:
        if media == PROTOBUF:
            export.ParseFromString(body)
        else:
            json_format.ParseDict(
                _hex_ids_to_base64(json.loads(body)), export, ignore_unknown_fields=True
            )
    except (
        DecodeError,
        json_format.ParseError,
        ValueError,
        TypeError,
        AttributeError,
        RecursionError,
    ) as e:
        raise HTTPException(400, f"body isn't an OTLP trace export: {e}") from e
    return export


def _hex_ids_to_base64(body: Any) -> Any:
    """OTLP/JSON is protobuf's JSON mapping except that IDs are hex, not base64. An ID that
    isn't hex becomes empty, so only its span is refused, not the whole export."""
    for rs in body.get("resourceSpans") or []:
        for ss in rs.get("scopeSpans") or []:
            for sp in ss.get("spans") or []:
                for obj, keys in [(sp, ("traceId", "spanId", "parentSpanId"))] + [
                    (link, ("traceId", "spanId")) for link in sp.get("links") or []
                ]:
                    for key in keys:
                        if obj.get(key):
                            obj[key] = _hex_to_base64(obj[key])
    return body


def _hex_to_base64(value: Any) -> str:
    try:
        return base64.b64encode(bytes.fromhex(value)).decode()
    except (TypeError, ValueError):
        return ""


def _spans(rs: Any) -> list[OtelSpan]:
    resource = _attributes(rs.resource.attributes)
    return [_span(sp, resource) for ss in rs.scope_spans for sp in ss.spans]


def _span(sp: ProtoSpan, resource: dict[str, Any]) -> OtelSpan:
    return OtelSpan(
        trace_id=sp.trace_id.hex(),
        span_id=sp.span_id.hex(),
        parent_span_id=sp.parent_span_id.hex() or None,
        parent_is_remote=bool(sp.flags & _HAS_IS_REMOTE and sp.flags & _IS_REMOTE),
        name=sp.name,
        start_time_ns=sp.start_time_unix_nano,
        end_time_ns=sp.end_time_unix_nano,
        status=_STATUS.get(sp.status.code, "unset"),
        attributes=_attributes(sp.attributes),
        events=[OtelEvent(e.name, _attributes(e.attributes)) for e in sp.events],
        resource=resource,
    )


def _attributes(kvs: Iterable[KeyValue]) -> dict[str, Any]:
    return {kv.key: _value(kv.value) for kv in kvs}


def _value(v: AnyValue) -> Any:
    match v.WhichOneof("value"):
        case "string_value":
            return v.string_value
        case "bool_value":
            return v.bool_value
        case "int_value":
            return v.int_value
        case "double_value":
            return v.double_value
        case "bytes_value":
            return v.bytes_value
        case "array_value":
            return [_value(x) for x in v.array_value.values]
        case "kvlist_value":
            return _attributes(v.kvlist_value.values)
        case _:
            return None
