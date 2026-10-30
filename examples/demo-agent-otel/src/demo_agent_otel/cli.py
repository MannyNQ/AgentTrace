"""`demo-agent-otel "<question>"`: answer one question, exporting its trace over OTLP/HTTP.

Configured the standard OpenTelemetry way, with `OTEL_EXPORTER_OTLP_ENDPOINT` (`<ingest>/otlp`) and
`OTEL_EXPORTER_OTLP_HEADERS` (`Authorization=Bearer%20<key>`). Without them it sends to the local
stack with `TRAYCE_API_KEY`, else the stack's default key, like the demo agent.
"""

import argparse
import json
import os
from urllib.parse import quote

from dotenv import find_dotenv, load_dotenv
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from demo_agent_otel.agent import run

SERVICE = "demo-agent-otel"


def main() -> None:
    load_dotenv(find_dotenv(usecwd=True))
    os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:8000/otlp")
    key = os.environ.get("TRAYCE_API_KEY") or "tr-local-dev-key"
    os.environ.setdefault("OTEL_EXPORTER_OTLP_HEADERS", f"Authorization={quote('Bearer ' + key)}")
    parser = argparse.ArgumentParser(description="Ask the Keyly support agent a question.")
    parser.add_argument("question")
    args = parser.parse_args()

    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    # The run's input on the root span, so "Add to dataset" and `trayce replay` have it:
    # the GenAI conventions don't say where an agent run's input goes.
    attributes = {
        "input.value": json.dumps({"question": args.question}),
        "input.mime_type": "application/json",
    }
    with trace.get_tracer(SERVICE).start_as_current_span(SERVICE, attributes=attributes) as root:
        answer = run(args.question)
        root.set_attribute("output.value", answer)
        trace_id = format(root.get_span_context().trace_id, "032x")
    provider.shutdown()  # flushes the batch
    print(answer)
    print(f"trace {trace_id}")


if __name__ == "__main__":
    main()
