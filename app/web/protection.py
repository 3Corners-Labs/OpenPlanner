"""Request protection for public HTML forms: same-origin checks and rate limits.

Forms carry no CSRF token. Browsers send ``Sec-Fetch-Site`` and ``Origin`` with every POST;
a cross-site POST is refused (``RejectedRequest``). Rate limits use slowapi, keyed by client
IP (behind a proxy, uvicorn's ``--proxy-headers`` supplies the real address).
"""

from dataclasses import dataclass

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


class RejectedRequest(Exception):  # noqa: N818 - reads as an outcome, like HTTPException
    """The request is refused before it reaches the domain (cross-site POST, bot, bad input).

    Args:
        status: HTTP status of the error page.
    """

    def __init__(self, status: int = 400) -> None:
        """Store the status."""
        super().__init__(status)
        self.status = status


def require_same_origin(request: Request) -> None:
    """FastAPI dependency: refuse POSTs that a browser marks as cross-site.

    Raises:
        RejectedRequest: 403 if ``Sec-Fetch-Site`` is cross-site or ``Origin`` differs.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        raise RejectedRequest(403)
    origin = request.headers.get("origin")
    if origin is not None and origin != f"{request.url.scheme}://{request.url.netloc}":
        raise RejectedRequest(403)


@dataclass
class RateLimits:
    """Current limit strings; set once per application in ``configure_rate_limits``."""

    bookings: str = "10/hour"
    reads: str = "60/minute"


LIMITS = RateLimits()
limiter = Limiter(key_func=get_remote_address)


def configure_rate_limits(*, enabled: bool, bookings: str, reads: str) -> None:
    """Apply the settings to the process-wide limiter and clear its counters."""
    limiter.enabled = enabled
    LIMITS.bookings = bookings
    LIMITS.reads = reads
    limiter.reset()


def booking_limit() -> str:
    """Limit for writes: booking, cancelling, rescheduling."""
    return LIMITS.bookings


def read_limit() -> str:
    """Limit for availability pages."""
    return LIMITS.reads
