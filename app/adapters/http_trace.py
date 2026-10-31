"""The ``outbound.request`` trace point on every outgoing HTTP call (ADR 0085 point 9).

Wraps the transport of the shared ``httpx.AsyncClient`` of the web and the worker process, so
every adapter's call is traced in one place with method, host, status and duration, or the
exception type when no response came. Never the URL (a query may carry a token), a header or
a body.

    client ─► TracingTransport.handle_async_request ─► inner transport ─► trace line
"""

from time import perf_counter

import httpx

from app.logging import tracer


class TracingTransport(httpx.AsyncBaseTransport):
    """An ``httpx`` transport that traces each request it passes on.

    Args:
        inner: The transport that does the work; the network one by default.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        """Keep the inner transport."""
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Pass the request on and trace its outcome; an exception is traced and re-raised."""
        started = perf_counter()
        try:
            response = await self._inner.handle_async_request(request)
        except Exception as exc:
            tracer.trace(
                "outbound.request",
                method=request.method,
                host=request.url.host,
                error=type(exc).__name__,
                ms=int((perf_counter() - started) * 1000),
            )
            raise
        tracer.trace(
            "outbound.request",
            method=request.method,
            host=request.url.host,
            status=response.status_code,
            ms=int((perf_counter() - started) * 1000),
        )
        return response

    async def aclose(self) -> None:
        """Close the inner transport."""
        await self._inner.aclose()


def traced_transport(inner: httpx.AsyncBaseTransport | None) -> httpx.AsyncBaseTransport:
    """Wrap ``inner`` (the network transport when ``None``) in a ``TracingTransport``."""
    return TracingTransport(inner or httpx.AsyncHTTPTransport())
