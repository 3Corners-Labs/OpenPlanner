"""Request protection for public HTML forms: same-origin checks and rate limits.

Forms carry no CSRF token. The primary signal is Fetch Metadata: browsers send
``Sec-Fetch-Site`` with every request and pages cannot forge it. Only when it is missing
(older browsers) is ``Origin`` compared instead. A cross-site POST is refused
(``RejectedRequest``).

Rate limits use slowapi, keyed by client IP (behind a proxy, uvicorn's ``--proxy-headers``
supplies the real address).
"""

from dataclasses import dataclass

import structlog
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

log = structlog.get_logger()


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

    ``Origin`` is not trusted when ``Sec-Fetch-Site`` is present: some referrer policies make
    browsers send ``Origin: null`` even for same-origin form posts.

    Raises:
        RejectedRequest: 403 if ``Sec-Fetch-Site`` is not same-origin/none, or, without it,
            if ``Origin`` differs from this site.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        if site in ("same-origin", "none"):
            return
        log.info("request.rejected", reason="cross_site", sec_fetch_site=site[:32])
        raise RejectedRequest(403)
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if origin is not None and origin != expected:
        # Origins are not personal data; logging them makes misconfigured proxies visible.
        log.info(
            "request.rejected", reason="origin_mismatch", origin=origin[:200], expected=expected
        )
        raise RejectedRequest(403)


@dataclass
class RateLimits:
    """Current limit strings; set once per application in ``configure_rate_limits``."""

    bookings: str = "10/hour"
    reads: str = "60/minute"
    assets: str = "600/minute"


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


def asset_limit() -> str:
    """Limit for branding assets (theme stylesheet, logo); cached by browsers, so generous."""
    return LIMITS.assets
