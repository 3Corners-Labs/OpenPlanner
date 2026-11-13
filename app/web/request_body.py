"""Reading a request body with a hard cap: the one place that does it.

A body is never read in full before its size is known. A declared length over the limit refuses
at once; without one (a chunked body) reading stops as soon as more than the limit has arrived,
so a body sent slowly or without end holds no more memory than the limit and no connection for
longer than it takes to pass it. The webhook gate (ADR 0124) and the machine API's configuration
package upload (ADR 0089) both read through it.
"""

from fastapi import Request

from app.domain.inbound_gate import declared_length_acceptable


async def read_capped(request: Request, limit: int) -> bytes | None:
    """Return the request body, or ``None`` if it is larger than ``limit`` bytes.

    Args:
        request: The request whose body is read.
        limit: The largest body in bytes.

    Returns:
        The body, or ``None`` from the declared ``Content-Length`` when it is over the limit, else
        as soon as the bytes read pass the limit; nothing more is read after that.
    """
    if not declared_length_acceptable(request.headers.get("content-length"), limit):
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            return None
    return bytes(body)
