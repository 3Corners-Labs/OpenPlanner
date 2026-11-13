"""Who is calling: the one reading of the caller's address, and the key a limiter counts it under.

The rule, for the whole application: the caller's address is the one the server derived
(``request.client``, which behind a proxy uvicorn's ``--proxy-headers`` has set from the trusted
proxy, ADR 0086). The forwarded headers are read here and nowhere else (``forwarded_of``, and
only to say what the server did with them), so nothing the caller sends can change the address.
Counted by caller, an IPv4 address counts as it is and an IPv6 address by its /64, so
rotating addresses within a prefix never gives a fresh bucket (ADR 0124 step 2a).

Rate limits, security events, the machine API, the instance probe and the display links all take
the address from here.
"""

from starlette.requests import Request

from app.domain.networks import caller_key
from app.domain.public_address import Forwarded, forwarded_state


def client_address(request: Request) -> str | None:
    """Return the caller's address as the server derived it, or ``None`` without one.

    No forwarded header is read here; a header the caller sends cannot change the result.
    """
    return request.client.host if request.client else None


def forwarded_of(request: Request) -> Forwarded:
    """Return what the server did with the forwarded headers of *this* request (ADR 0086).

    The one place the application reads ``X-Forwarded-For`` and ``X-Forwarded-Proto``, through
    the one judgement ``forwarded_state``: *honoured* (the server used them, so the client address
    is not the peer's), *ignored* (they are there and the client address and scheme are still the
    peer's, because the peer is not a trusted proxy) or *absent*. The instance probe, the proxy
    watch's middleware and the webhook gate all ask here. A value is never used as an address.
    """
    return forwarded_state(
        forwarded_for=request.headers.get("x-forwarded-for"),
        forwarded_proto=request.headers.get("x-forwarded-proto"),
        client=client_address(request),
        scheme=request.url.scheme,
    )


def forwarded_headers_ignored(request: Request) -> bool:
    """Return whether *this* request carries forwarded headers that the server did not use.

    Then the address held for the call is a hop that is not trusted (ADR 0124 point 4).
    """
    return forwarded_of(request) is Forwarded.IGNORED


def caller_of(request: Request) -> str:
    """Return the key every limiter counts the caller of ``request`` under.

    An IPv4 caller counts by its address, an IPv6 caller by its /64, an IPv4-mapped IPv6 address
    as its IPv4 form (``app.domain.networks.caller_key``).
    """
    return caller_key(client_address(request))
