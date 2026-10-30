import base64
import gzip
import json
from uuid import UUID

import redis.exceptions
from fake_auth import FakeAuth
from fastapi.testclient import TestClient
from google.protobuf import json_format
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span, Status

from trayce_ingest.app import create_app
from trayce_ingest.settings import Settings

PROJECT = UUID("00000000-0000-0000-0000-0000000000aa")
TRACE = "0af7651916cd43dd8448eb211c80319c"
ROOT = "b7ad6b7169203331"
CHILD = "00f067aa0ba902b7"
T0_NS = 1_767_225_600_000_000_000
PROTOBUF = {"Content-Type": "application/x-protobuf", "Authorization": "Bearer good"}
JSON = {"Content-Type": "application/json", "Authorization": "Bearer good"}


class Queue:
    def __init__(self, fail: bool = False):
        self.pushed: list[tuple[UUID, list]] = []
        self.fail = fail

    async def push(self, project_id: UUID, spans_json: str) -> None:
        if self.fail:
            raise redis.exceptions.ConnectionError("Connection refused")
        self.pushed.append((project_id, json.loads(spans_json)))


def setup(fail: bool = False, **settings):
    q = Queue(fail)
    app = create_app(
        Settings(**settings), checks={}, auth_store=FakeAuth.with_keys({"good": PROJECT}), queue=q
    )
    return TestClient(app), q


def attr(key: str, value) -> KeyValue:
    if isinstance(value, bool):
        v = AnyValue(bool_value=value)
    elif isinstance(value, int):
        v = AnyValue(int_value=value)
    elif isinstance(value, float):
        v = AnyValue(double_value=value)
    else:
        v = AnyValue(string_value=value)
    return KeyValue(key=key, value=v)


def otel_span(span_id=CHILD, parent=ROOT, attributes=None, **over) -> Span:
    fields = {
        "trace_id": bytes.fromhex(TRACE),
        "span_id": bytes.fromhex(span_id),
        "parent_span_id": bytes.fromhex(parent) if parent else b"",
        "name": "work",
        "start_time_unix_nano": T0_NS,
        "end_time_unix_nano": T0_NS + 1_000_000,
        "attributes": [attr(k, v) for k, v in (attributes or {}).items()],
    }
    return Span(**(fields | over))


def request(*spans: Span, resource=None) -> ExportTraceServiceRequest:
    rs = ResourceSpans(scope_spans=[ScopeSpans(spans=list(spans))])
    for k, v in (resource or {}).items():
        rs.resource.attributes.append(attr(k, v))
    return ExportTraceServiceRequest(resource_spans=[rs])


def as_json(req: ExportTraceServiceRequest) -> bytes:
    """OTLP/JSON: protobuf's JSON mapping, except that IDs are hex rather than base64."""
    body = json_format.MessageToDict(req)
    for rs in body.get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            for s in ss.get("spans", []):
                for k in ("traceId", "spanId", "parentSpanId"):
                    if k in s:
                        s[k] = base64.b64decode(s[k]).hex()
    return json.dumps(body).encode()


TOOL = {
    "gen_ai.operation.name": "execute_tool",
    "gen_ai.tool.name": "lookup_order",
    "gen_ai.tool.call.arguments": '{"order_id": "42"}',
}


def test_a_protobuf_export_is_translated_and_queued_under_the_keys_project():
    c, q = setup()
    req = request(
        otel_span(span_id=ROOT, parent=None, name="POST /ask"),
        otel_span(attributes=TOOL),
        resource={"service.name": "support-bot"},
    )
    r = c.post("/otlp/v1/traces", content=req.SerializeToString(), headers=PROTOBUF)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/x-protobuf"
    resp = ExportTraceServiceResponse.FromString(r.content)
    assert not resp.HasField("partial_success")

    project_id, queued = q.pushed[0]
    assert project_id == PROJECT
    root, tool = queued
    assert root["parent_span_id"] is None and root["kind"] == "custom"
    assert root["attributes"]["resource.service.name"] == "support-bot"
    assert tool["kind"] == "tool" and tool["name"] == "lookup_order"
    assert tool["input"] == {"order_id": "42"}
    assert tool["trace_id"] == TRACE and tool["parent_span_id"] == ROOT
    assert tool["start_time"].startswith("2026-01-01T00:00:00")


def test_a_json_export_is_answered_in_json():
    c, q = setup()
    req = request(otel_span(attributes=TOOL))
    r = c.post("/otlp/v1/traces", content=as_json(req), headers=JSON)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/json"
    assert r.json() == {}
    assert q.pushed[0][1][0]["span_id"] == CHILD


def test_a_gzipped_body_is_accepted():
    c, q = setup()
    body = gzip.compress(request(otel_span()).SerializeToString())
    r = c.post("/otlp/v1/traces", content=body, headers=PROTOBUF | {"Content-Encoding": "gzip"})
    assert r.status_code == 200 and len(q.pushed[0][1]) == 1


def test_spans_that_cant_be_stored_are_dropped_and_reported():
    c, q = setup()
    req = request(
        otel_span(),
        otel_span(span_id="0" * 16),
        otel_span(span_id="1111111111111111", start_time_unix_nano=0),
    )
    r = c.post("/otlp/v1/traces", content=req.SerializeToString(), headers=PROTOBUF)
    assert r.status_code == 200
    resp = ExportTraceServiceResponse.FromString(r.content)
    assert resp.partial_success.rejected_spans == 2
    assert "span ID" in resp.partial_success.error_message
    assert [s["span_id"] for s in q.pushed[0][1]] == [CHILD]


def test_partial_success_in_json():
    c, _ = setup()
    r = c.post(
        "/otlp/v1/traces", content=as_json(request(otel_span(span_id="0" * 16))), headers=JSON
    )
    body = r.json()
    assert body["partialSuccess"]["rejectedSpans"] == "1"  # int64 is a string in OTLP/JSON
    assert "span ID" in body["partialSuccess"]["errorMessage"]


def test_an_export_with_nothing_storable_queues_nothing():
    c, q = setup()
    r = c.post(
        "/otlp/v1/traces",
        content=request(otel_span(span_id="0" * 16)).SerializeToString(),
        headers=PROTOBUF,
    )
    assert r.status_code == 200 and q.pushed == []


def test_status_and_remote_parents_come_through():
    c, q = setup()
    remote = 0x100 | 0x200  # SPAN_FLAGS_CONTEXT_HAS_IS_REMOTE, SPAN_FLAGS_CONTEXT_IS_REMOTE
    req = request(
        otel_span(flags=remote, status=Status(code=Status.STATUS_CODE_ERROR)),
        otel_span(span_id="1111111111111111", flags=0x100),
    )
    c.post("/otlp/v1/traces", content=req.SerializeToString(), headers=PROTOBUF)
    remote_child, local_child = q.pushed[0][1]
    assert remote_child["parent_span_id"] is None and remote_child["status"] == "error"
    assert local_child["parent_span_id"] == ROOT


def test_array_and_kvlist_attribute_values_are_decoded():
    c, q = setup()
    s = otel_span(parent=None)
    tags = s.attributes.add(key="tag.tags")
    for t in ("prod", "beta"):
        tags.value.array_value.values.add(string_value=t)
    kv = s.attributes.add(key="config")
    kv.value.kvlist_value.values.append(attr("retries", 3))
    c.post("/otlp/v1/traces", content=request(s).SerializeToString(), headers=PROTOBUF)
    queued = q.pushed[0][1][0]
    assert queued["trace"]["tags"] == ["prod", "beta"]
    assert json.loads(queued["attributes"]["config"]) == {"retries": 3}


def test_a_body_that_cant_be_decoded_is_400():
    c, q = setup()
    assert c.post("/otlp/v1/traces", content=b"\xff\xff", headers=PROTOBUF).status_code == 400
    assert c.post("/otlp/v1/traces", content=b"{not json", headers=JSON).status_code == 400
    bad_gzip = PROTOBUF | {"Content-Encoding": "gzip"}
    assert c.post("/otlp/v1/traces", content=b"plain", headers=bad_gzip).status_code == 400
    assert q.pushed == []


def test_an_unknown_content_type_is_415():
    c, _ = setup()
    r = c.post(
        "/otlp/v1/traces",
        content=b"x",
        headers={"Content-Type": "text/plain", "Authorization": "Bearer good"},
    )
    assert r.status_code == 415


def test_too_many_spans_is_400():
    c, q = setup(max_spans_per_request=2)
    spans = [otel_span(span_id=f"{i + 1:016x}") for i in range(3)]
    r = c.post("/otlp/v1/traces", content=request(*spans).SerializeToString(), headers=PROTOBUF)
    assert r.status_code == 400 and q.pushed == []


def test_the_size_limit_applies_after_decompression():
    c, q = setup(max_request_bytes=2000)
    big = otel_span(attributes={"blob": "a" * 5000})
    body = gzip.compress(request(big).SerializeToString())
    assert len(body) < 2000
    r = c.post("/otlp/v1/traces", content=body, headers=PROTOBUF | {"Content-Encoding": "gzip"})
    assert r.status_code == 400 and q.pushed == []


def test_a_missing_or_unknown_key_is_401():
    c, q = setup()
    body = request(otel_span()).SerializeToString()
    no_key = {"Content-Type": "application/x-protobuf"}
    assert c.post("/otlp/v1/traces", content=body, headers=no_key).status_code == 401
    bad_key = PROTOBUF | {"Authorization": "Bearer nope"}
    assert c.post("/otlp/v1/traces", content=body, headers=bad_key).status_code == 401
    assert q.pushed == []


def test_redis_being_down_is_a_retryable_503():
    c, _ = setup(fail=True)
    r = c.post(
        "/otlp/v1/traces", content=request(otel_span()).SerializeToString(), headers=PROTOBUF
    )
    assert r.status_code == 503
    assert r.headers["retry-after"]


def test_resource_attributes_dont_multiply_an_export_by_its_root_count():
    c, q = setup()
    roots = [otel_span(span_id=f"{i + 1:016x}", parent=None) for i in range(1000)]
    resource = {f"r{i}": "x" * 4096 for i in range(250)}  # 1 MB of resource
    body = request(*roots, resource=resource).SerializeToString()
    assert c.post("/otlp/v1/traces", content=body, headers=PROTOBUF).status_code == 200
    queued = len(json.dumps(q.pushed[0][1]))
    assert queued < 5 * 1024 * 1024  # it was about 1 GB


def test_a_span_that_fails_to_translate_is_rejected_alone(monkeypatch):
    import trayce_ingest.otlp as otlp

    real = otlp.translate

    def flaky(o):
        if o.span_id == "1111111111111111":
            raise RuntimeError("translator bug")
        return real(o)

    monkeypatch.setattr(otlp, "translate", flaky)
    c, q = setup()
    req = request(otel_span(), otel_span(span_id="1111111111111111"))
    r = c.post("/otlp/v1/traces", content=req.SerializeToString(), headers=PROTOBUF)
    assert r.status_code == 200
    assert ExportTraceServiceResponse.FromString(r.content).partial_success.rejected_spans == 1
    assert [s["span_id"] for s in q.pushed[0][1]] == [CHILD]


def test_a_deeply_nested_json_body_is_400():
    c, q = setup()
    r = c.post("/otlp/v1/traces", content=b"[" * 200_000, headers=JSON)
    assert r.status_code == 400 and q.pushed == []
