"""Background batching exporter. Tracing must never break or slow the host app, so every failure
here is logged and swallowed, a full queue drops spans instead of blocking, and the flush at exit
gives up after a short deadline instead of retrying."""

import atexit
import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from agenttrace import _refusals as refusals
from agenttrace.spans import Span

log = logging.getLogger("agenttrace")
KEY_FROM = "AGENTTRACE_API_KEY, or the api_key passed to agenttrace.configure()"
MAX_REFUSAL = 500

DEFAULT_ENDPOINT = "http://localhost:8000"
TRUNCATED_ATTRIBUTE = "agenttrace.truncated"


@dataclass
class Config:
    api_key: str | None = None
    endpoint: str = DEFAULT_ENDPOINT
    batch_size: int = 100
    # Ingest refuses requests over 5 MB (ADR-0017); stay under it with room for the envelope.
    max_batch_bytes: int = 4 * 1024 * 1024
    flush_interval_s: float = 1.0
    max_queue: int = 10_000
    retries: int = 3
    timeout_s: float = 5.0
    shutdown_timeout_s: float = 2.0

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            api_key=os.environ.get("AGENTTRACE_API_KEY") or None,
            endpoint=os.environ.get("AGENTTRACE_ENDPOINT", DEFAULT_ENDPOINT),
        )


def _truncate(value: object, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text.encode()[:limit].decode("utf-8", errors="ignore")


def _size(value: object) -> int:
    if value is None:
        return 0
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return len(text.encode())


def encode_span(span: Span, max_bytes: int) -> bytes | None:
    """The span as JSON, under `max_bytes`. An oversized input or output is cut to a prefix and
    the span is marked with `agenttrace.truncated`; None if it still can't fit."""
    data = span.model_dump_json().encode()
    if len(data) <= max_bytes:
        return data
    # Leave a quarter of the budget for everything but the two payloads.
    limit = max_bytes * 3 // 8
    fields = [f for f in ("input", "output") if _size(getattr(span, f)) > limit]
    cut = span.model_copy(
        update={f: _truncate(getattr(span, f), limit) for f in fields}
        | {"attributes": {**span.attributes, TRUNCATED_ATTRIBUTE: ",".join(fields)}}
    )
    data = cut.model_dump_json().encode()
    return data if len(data) <= max_bytes else None


class Exporter:
    def __init__(self, config: Config):
        self.config = config
        self._queue: queue.Queue[Span] = queue.Queue(maxsize=config.max_queue)
        self._carry: bytes | None = None  # a span that didn't fit in the previous batch
        self._dropped = 0
        self._lock = threading.Lock()  # one batch in flight at a time: the thread or flush()
        self._wake = threading.Event()
        self._closing = threading.Event()  # set at shutdown: in-flight retries stop waiting
        self._thread = threading.Thread(target=self._run, name="agenttrace-exporter", daemon=True)
        self._thread.start()

    def export(self, span: Span) -> None:
        try:
            self._queue.put_nowait(span)
        except queue.Full:
            self._drop(1, "export queue full")
            return
        if self._queue.qsize() >= self.config.batch_size:
            self._wake.set()

    def flush(self, timeout_s: float | None = None) -> None:
        """Send everything queued so far, retrying failures. With a timeout (as at exit), each
        batch is tried once, and the first failure or the deadline drops what's left."""
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while self._carry is not None or not self._queue.empty():
            if not self._send_one_batch(deadline) and deadline is not None:
                self._drop_remaining("giving up at shutdown")
                return

    def shutdown(self) -> None:
        self._closing.set()
        self.flush(timeout_s=self.config.shutdown_timeout_s)

    def _run(self) -> None:
        while True:
            self._wake.wait(self.config.flush_interval_s)
            self._wake.clear()
            self.flush()

    def _send_one_batch(self, deadline: float | None) -> bool:
        timeout = None if deadline is None else max(deadline - time.monotonic(), 0)
        if not self._lock.acquire(timeout=-1 if timeout is None else timeout):
            return False
        try:
            parts = self._take_batch()
            if not parts:
                return True
            body = b'{"spans":[' + b",".join(parts) + b"]}"
            return self._post(body, len(parts), deadline)
        finally:
            self._lock.release()

    def _take_batch(self) -> list[bytes]:
        parts: list[bytes] = []
        size = 0
        while len(parts) < self.config.batch_size:
            if self._carry is not None:
                data, self._carry = self._carry, None
            else:
                try:
                    span = self._queue.get_nowait()
                except queue.Empty:
                    break
                data = encode_span(span, self.config.max_batch_bytes)
                if data is None:
                    self._drop(1, f"span {span.name!r} is too large to send")
                    continue
            if parts and size + len(data) > self.config.max_batch_bytes:
                self._carry = data  # starts the next batch
                break
            parts.append(data)
            size += len(data) + 1  # +1 for the comma between spans
        return parts

    def _post(self, body: bytes, count: int, deadline: float | None) -> bool:
        req = urllib.request.Request(
            f"{self.config.endpoint.rstrip('/')}/v1/spans",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
        )
        attempts = 1 if deadline is not None else self.config.retries + 1
        error: Exception | None = None
        for attempt in range(attempts):
            timeout = self.config.timeout_s
            if deadline is not None:
                timeout = min(timeout, deadline - time.monotonic())
                if timeout <= 0:
                    break
            try:
                with urllib.request.urlopen(req, timeout=timeout):
                    return True
            except urllib.error.HTTPError as e:
                if 400 <= e.code < 500:  # our bug or a bad key: retrying won't help
                    why = refusals.explain(e.code, refusals.reason(e.read()), KEY_FROM)
                    if len(why) > MAX_REFUSAL:  # a bad batch lists an error per field
                        why = why[:MAX_REFUSAL] + "…"
                    self._drop(count, f"ingest rejected them: {e.code} {why}")
                    return True  # the batch is settled; later batches may still succeed
                error = e
            except (urllib.error.URLError, OSError) as e:
                error = e
            if attempt < attempts - 1 and self._closing.wait(0.5 * 2**attempt):
                break  # shutting down: don't hold up exit with retries
        self._drop(count, f"could not reach ingest: {error}")
        return False

    def _drop_remaining(self, reason: str) -> None:
        count = 1 if self._carry is not None else 0
        self._carry = None
        while True:
            try:
                self._queue.get_nowait()
                count += 1
            except queue.Empty:
                break
        if count:
            self._drop(count, reason)

    def _drop(self, count: int, reason: str) -> None:
        self._dropped += count
        log.warning(
            "agenttrace: dropped %d spans (%s); %d dropped so far", count, reason, self._dropped
        )


_config: Config | None = None
_exporter: Exporter | None = None
_disabled_warned = False
_state_lock = threading.Lock()


def configure(
    api_key: str | None = None, endpoint: str | None = None, **options: float | int
) -> None:
    """Override settings from AGENTTRACE_API_KEY / AGENTTRACE_ENDPOINT. Call before tracing."""
    global _config, _exporter
    base = Config.from_env()
    with _state_lock:
        _config = Config(
            api_key=api_key or base.api_key,
            endpoint=endpoint or base.endpoint,
            **options,  # type: ignore[arg-type]
        )
        _exporter = None


def get_exporter() -> Exporter | None:
    """The process-wide exporter, or None when no API key is set (tracing is then a no-op)."""
    global _config, _exporter, _disabled_warned
    with _state_lock:
        if _exporter is not None:
            return _exporter
        _config = _config or Config.from_env()
        if not _config.api_key:
            if not _disabled_warned:
                log.warning("agenttrace: AGENTTRACE_API_KEY is not set; spans are not sent")
                _disabled_warned = True
            return None
        _exporter = Exporter(_config)
        atexit.register(_exporter.shutdown)
        return _exporter


def flush(timeout_s: float | None = None) -> None:
    if _exporter is not None:
        _exporter.flush(timeout_s)
