import json
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from trayce import tracing
from trayce.spans import Span


class Collector:
    def __init__(self):
        self.spans: list[Span] = []

    def export(self, span: Span) -> None:
        self.spans.append(span)

    def by_name(self, name: str) -> Span:
        return next(s for s in self.spans if s.name == name)


@pytest.fixture
def collected(monkeypatch) -> Collector:
    c = Collector()
    monkeypatch.setattr(tracing, "get_exporter", lambda: c)
    return c


Handler = Callable[[str, dict, bytes], tuple[int, dict]]


@pytest.fixture
def http_server() -> Iterator[Callable[[Handler], str]]:
    """Start a local HTTP server answering POSTs with `handler(path, headers, body)`."""
    servers: list[ThreadingHTTPServer] = []

    def start(handler: Handler) -> str:
        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                status, payload = handler(self.path, dict(self.headers), body)
                out = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for s in servers:
        s.shutdown()
