"""One request-body limit for every route."""

from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodyLimit:
    """Answers 413 when a request body is over `max_bytes`. The limit is checked as the body is
    read, so a request with no Content-Length, or one that understates it, is still cut off. The
    413 is raised from inside the route's body read, where FastAPI turns it into a response."""

    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        declared = Headers(scope=scope).get("content-length")
        seen = 0

        async def limited() -> Message:
            nonlocal seen
            if declared and int(declared) > self.max_bytes:
                raise self._too_large()
            message = await receive()
            seen += len(message.get("body", b""))
            if seen > self.max_bytes:
                raise self._too_large()
            return message

        await self.app(scope, limited, send)

    def _too_large(self) -> HTTPException:
        return HTTPException(413, f"request body over {self.max_bytes} bytes")
