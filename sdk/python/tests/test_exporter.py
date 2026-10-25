import json
import time
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


def test_batches_stay_under_the_byte_limit(http_server):
    sizes: list[int] = []
    url = http_server(lambda p, h, b: (sizes.append(len(b)), (202, {}))[1])
    ex = Exporter(Config(api_key="k", endpoint=url, max_batch_bytes=250_000, flush_interval_s=60))
    for _ in range(5):
        s = make_span()
        ex.export(s.model_copy(update={"input": "x" * 100_000}))
    ex.flush()
    assert len(sizes) == 3  # 2 + 2 + 1 spans, never one oversized request
    assert all(size <= 250_000 + 100 for size in sizes)


def test_an_oversized_span_is_truncated_and_marked_not_dropped(http_server):
    bodies: list[bytes] = []
    url = http_server(lambda p, h, b: (bodies.append(b), (202, {}))[1])
    ex = Exporter(Config(api_key="k", endpoint=url, max_batch_bytes=50_000, flush_interval_s=60))
    big = make_span().model_copy(update={"input": {"messages": "é" * 100_000}, "output": "ok"})
    ex.export(big)
    ex.flush()
    assert len(bodies[0]) <= 50_000 + 20  # the wire size, plus the {"spans":[...]} envelope
    sent = json.loads(bodies[0])["spans"][0]
    assert sent["span_id"] == big.span_id
    assert sent["attributes"]["agenttrace.truncated"] == "input"  # the small output is kept
    assert sent["output"] == "ok"
    assert sent["input"].startswith('{"messages": "ééé')


def test_shutdown_with_ingest_down_is_fast_and_drops():
    ex = Exporter(
        Config(
            api_key="k", endpoint="http://127.0.0.1:9", flush_interval_s=60, shutdown_timeout_s=2
        )
    )
    for _ in range(500):
        ex.export(make_span())
    start = time.monotonic()
    ex.shutdown()
    assert time.monotonic() - start < 1.0  # one failed attempt, then the rest is dropped
    assert ex._dropped == 500 and ex._queue.empty()


def test_shutdown_respects_its_deadline_on_a_hanging_server(http_server):
    def hang(p, h, b):
        time.sleep(3)
        return 202, {}

    ex = Exporter(
        Config(api_key="k", endpoint=http_server(hang), flush_interval_s=60, shutdown_timeout_s=0.5)
    )
    ex.export(make_span())
    start = time.monotonic()
    ex.shutdown()
    assert time.monotonic() - start < 1.5
