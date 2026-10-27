"""OpenTelemetry spans as AgentTrace spans (ADR-0035). Importing this needs no OpenTelemetry
package: callers decode their own input (OTLP in ingest, SDK spans in the bridge) into `OtelSpan`.
"""

from agenttrace.otel.mapping import OtelEvent, OtelSpan, Unstorable, translate

__all__ = ["OtelEvent", "OtelSpan", "Unstorable", "translate"]
