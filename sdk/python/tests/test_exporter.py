import json
from datetime import UTC, datetime

from agenttrace import Span, SpanKind, new_span_id, new_trace_id
from agenttrace.exporter import Config, Exporter


def make_span(name="s") -> Span:
    return Span(
        trace_id=new_trace_id(),
        span_id=new_span_id(),
        name=name,
        kind=SpanKind.CUSTOM,
        start_time=datetime.now(UTC),
    )


def test_batches_are_posted_with_the_api_key(http_server):
    received: list[tuple[str, dict, dict]] = []
    url = http_server(lambda p, h, b: (received.append((p, h, json.loads(b))), (202, {}))[1])
    ex = Exporter(Config(api_key="k1", endpoint=url, batch_size=2, flush_interval_s=60))

    for i in range(3):
        ex.export(make_span(f"s{i}"))
    ex.flush()

    path, headers, _ = received[0]
    assert path == "/v1/spans" and headers["Authorization"] == "Bearer k1"
    names = [s["name"] for _, _, body in received for s in body["spans"]]
    assert names == ["s0", "s1", "s2"]
    assert [len(body["spans"]) for _, _, body in received] == [2, 1]


def test_server_errors_are_retried(http_server):
    calls = []

    def handler(p, h, b):
        calls.append(1)
        return (503, {}) if len(calls) < 3 else (202, {})

    ex = Exporter(Config(api_key="k", endpoint=http_server(handler), flush_interval_s=60))
    ex.export(make_span())
    ex.flush()
    assert len(calls) == 3


def test_client_errors_are_not_retried(http_server):
    calls = []
    url = http_server(lambda p, h, b: (calls.append(1), (422, {"detail": "bad"}))[1])
    ex = Exporter(Config(api_key="k", endpoint=url, flush_interval_s=60))
    ex.export(make_span())
    ex.flush()
    assert len(calls) == 1


def test_unreachable_server_drops_without_raising():
    ex = Exporter(
        Config(api_key="k", endpoint="http://127.0.0.1:9", retries=1, flush_interval_s=60)
    )
    ex.export(make_span())
    ex.flush()  # logs a warning; must not raise


def test_full_queue_drops_instead_of_blocking():
    ex = Exporter(Config(api_key="k", endpoint="http://127.0.0.1:9", max_queue=2, retries=0))
    ex._lock.acquire()  # stall the sender so the queue fills
    try:
        for _ in range(5):
            ex.export(make_span())
        assert ex._dropped == 3
    finally:
        ex._lock.release()
