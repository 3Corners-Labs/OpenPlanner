"""Name resolution and HTTP for the outbound guard (ADR 0083 point 8), without a network."""

from collections.abc import Callable

import httpx

from app.adapters.outbound import Resolver


def resolver(*addresses: str) -> Resolver:
    """Return a resolver that answers ``addresses`` for every host, as the system resolver would."""

    def resolve(host: str, port: int) -> list[str]:
        return list(addresses)

    return resolve


def mock(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    """Return a client whose every request is answered by ``handler`` instead of a server."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
