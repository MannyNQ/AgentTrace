"""OpenTelemetry spans as Trayce spans (ADR-0035, ADR-0036). The translator needs no
OpenTelemetry package: callers decode their own input (OTLP in ingest, SDK spans in the bridge)
into `OtelSpan`. `install()` needs the `trayce[otel]` extra.
"""

from trayce.otel.mapping import OtelEvent, OtelSpan, Unstorable, translate


def install() -> None:
    """Turn OpenTelemetry spans in this process into spans, sent with the SDK's own, and share
    OpenTelemetry's context, so the two kinds nest in one trace."""
    from trayce.otel.bridge import install as install_bridge

    install_bridge()


def install_if_available() -> bool:
    """`install()` when OpenTelemetry's SDK is importable; the eval runner calls this."""
    try:
        import opentelemetry.sdk.trace  # noqa: F401
    except ImportError:
        return False
    install()
    return True


__all__ = ["OtelEvent", "OtelSpan", "Unstorable", "install", "install_if_available", "translate"]
