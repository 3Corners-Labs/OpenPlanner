"""Who is calling: the one reading of the caller's address, and the key a limiter counts it under.

The rule, for the whole application: the caller's address is the one the server derived
(``request.client``, which behind a proxy uvicorn's ``--proxy-headers`` has set from the trusted
proxy, ADR 0086). A forwarded header is never read anywhere else, so nothing the caller sends can
change it. Counted by caller, an IPv4 address counts as it is and an IPv6 address by its /64, so
rotating addresses within a prefix never gives a fresh bucket (ADR 0124 step 2a).

Rate limits, security events, the machine API, the instance probe and the display links all take
the address from here.
"""

from starlette.requests import Request

from app.domain.networks import caller_key


def client_address(request: Request) -> str | None:
    """Return the caller's address as the server derived it, or ``None`` without one.

    No forwarded header is ever read; a header the caller sends cannot change the result.
    """
    return request.client.host if request.client else None


def caller_of(request: Request) -> str:
    """Return the key every limiter counts the caller of ``request`` under.

    An IPv4 caller counts by its address, an IPv6 caller by its /64, an IPv4-mapped IPv6 address
    as its IPv4 form (``app.domain.networks.caller_key``).
    """
    return caller_key(client_address(request))
