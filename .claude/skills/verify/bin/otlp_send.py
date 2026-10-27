"""Send one GenAI-convention trace with the official OpenTelemetry SDK and OTLP/HTTP exporter
(protobuf, gzipped), for the otlp recipe: an `invoke_agent` root, a priced `chat` span with cached
tokens, and a failed `execute_tool` span. Prints the trace ID.

    uv run python .claude/skills/verify/bin/otlp_send.py http://127.0.0.1:18000 <ingest key>
"""

import json
import sys

from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace import Status, StatusCode

base, key = sys.argv[1].rstrip("/"), sys.argv[2]
exporter = OTLPSpanExporter(
    endpoint=f"{base}/otlp/v1/traces",
    headers={"Authorization": f"Bearer {key}"},
    compression=Compression.Gzip,
)
provider = TracerProvider(resource=Resource.create({"service.name": "otlp-verify-bot"}))
provider.add_span_processor(SimpleSpanProcessor(exporter))
tracer = provider.get_tracer("verify")

root_attrs = {
    "gen_ai.operation.name": "invoke_agent",
    "gen_ai.conversation.id": "conv-verify-1",
    "enduser.id": "user-7",
}
chat_attrs = {
    "gen_ai.operation.name": "chat",
    "gen_ai.provider.name": "anthropic",
    "gen_ai.request.model": "claude-sonnet-5-5",
    "gen_ai.response.model": "claude-sonnet-5-5",
    "gen_ai.usage.input_tokens": 1500,
    "gen_ai.usage.output_tokens": 40,
    "gen_ai.usage.cache_read.input_tokens": 1000,
    "gen_ai.input.messages": json.dumps(
        [{"role": "user", "parts": [{"type": "text", "content": "Where is order 1042?"}]}]
    ),
}
tool_attrs = {
    "gen_ai.operation.name": "execute_tool",
    "gen_ai.tool.name": "lookup_order",
    "gen_ai.tool.call.arguments": json.dumps({"order_id": "1042"}),
    "error.type": "OrderNotFound",
}
with tracer.start_as_current_span("invoke_agent support", attributes=root_attrs) as root:
    with tracer.start_as_current_span("chat claude-sonnet-5-5", attributes=chat_attrs):
        pass
    with tracer.start_as_current_span("execute_tool lookup_order", attributes=tool_attrs) as tool:
        tool.set_status(Status(StatusCode.ERROR))
    trace_id = format(root.get_span_context().trace_id, "032x")
provider.shutdown()
print(trace_id)
