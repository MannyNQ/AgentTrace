"""Background batching exporter. Tracing must never break or slow the host app, so every failure
here is logged and swallowed, and a full queue drops spans instead of blocking."""

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

from agenttrace.spans import Span

log = logging.getLogger("agenttrace")

DEFAULT_ENDPOINT = "http://localhost:8000"


@dataclass
class Config:
    api_key: str | None = None
    endpoint: str = DEFAULT_ENDPOINT
    batch_size: int = 100
    flush_interval_s: float = 1.0
    max_queue: int = 10_000
    retries: int = 3
    timeout_s: float = 5.0

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            api_key=os.environ.get("AGENTTRACE_API_KEY") or None,
            endpoint=os.environ.get("AGENTTRACE_ENDPOINT", DEFAULT_ENDPOINT),
        )


class Exporter:
    def __init__(self, config: Config):
        self.config = config
        self._queue: queue.Queue[Span] = queue.Queue(maxsize=config.max_queue)
        self._dropped = 0
        self._lock = threading.Lock()  # one batch in flight at a time: the thread or flush()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, name="agenttrace-exporter", daemon=True)
        self._thread.start()

    def export(self, span: Span) -> None:
        try:
            self._queue.put_nowait(span)
        except queue.Full:
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 1000 == 0:
                log.warning("agenttrace: export queue full, %d spans dropped", self._dropped)
            return
        if self._queue.qsize() >= self.config.batch_size:
            self._wake.set()

    def flush(self) -> None:
        """Send everything queued so far. Called at exit; safe to call any time."""
        while not self._queue.empty():
            self._send_one_batch()

    def _run(self) -> None:
        while True:
            self._wake.wait(self.config.flush_interval_s)
            self._wake.clear()
            self.flush()

    def _send_one_batch(self) -> None:
        with self._lock:
            batch: list[Span] = []
            while len(batch) < self.config.batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            if batch:
                self._post(batch)

    def _post(self, batch: list[Span]) -> None:
        body = json.dumps({"spans": [s.model_dump(mode="json") for s in batch]}).encode()
        req = urllib.request.Request(
            f"{self.config.endpoint.rstrip('/')}/v1/spans",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
        )
        error: Exception | None = None
        for attempt in range(self.config.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout_s):
                    return
            except urllib.error.HTTPError as e:
                if 400 <= e.code < 500:  # our bug or a bad key: retrying won't help
                    detail = e.read()[:500].decode(errors="replace")
                    log.warning(
                        "agenttrace: ingest rejected %d spans: %s %s", len(batch), e.code, detail
                    )
                    return
                error = e
            except (urllib.error.URLError, OSError) as e:
                error = e
            if attempt < self.config.retries:
                time.sleep(0.5 * 2**attempt)
        log.warning(
            "agenttrace: dropped %d spans after %d retries: %s",
            len(batch),
            self.config.retries,
            error,
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
        atexit.register(_exporter.flush)
        return _exporter


def flush() -> None:
    if _exporter is not None:
        _exporter.flush()
