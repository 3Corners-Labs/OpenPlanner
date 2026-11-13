"""Who is calling: the one reading of the caller's address, and the key a limiter counts it under.

The rule, for the whole application: the caller's address is the one the server derived
(``request.client``, which behind a proxy uvicorn's ``--proxy-headers`` has set from the trusted
proxy, ADR 0086). A forwarded header is read nowhere else (and here only for its presence, to
say that this call's were ignored), so nothing the caller sends can change the address.
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

    No forwarded header is ever read; a header the caller sends cannot change the result.
    """
    return request.client.host if request.client else None


def forwarded_headers_ignored(request: Request) -> bool:
    """Return whether *this* request carries forwarded headers that the server did not use.

    That is the judgement of ``forwarded_state`` (ADR 0086) for one request: the headers are there
    and the client address and scheme are still the peer's, because the peer is not a trusted
    proxy. The address held for the call is then a hop that is not trusted. Only the presence of
    ``X-Forwarded-For`` and ``X-Forwarded-Proto`` is looked at, never a value used as an address.
    """
    return (
        forwarded_state(
            forwarded_for=request.headers.get("x-forwarded-for"),
            forwarded_proto=request.headers.get("x-forwarded-proto"),
            client=client_address(request),
            scheme=request.url.scheme,
        )
        is Forwarded.IGNORED
    )


def caller_of(request: Request) -> str:
    """Return the key every limiter counts the caller of ``request`` under.

    An IPv4 caller counts by its address, an IPv6 caller by its /64, an IPv4-mapped IPv6 address
    as its IPv4 form (``app.domain.networks.caller_key``).
    """
    return caller_key(client_address(request))
