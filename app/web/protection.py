"""Request protection for public HTML forms: same-origin checks and rate limits.

Forms carry no CSRF token. The primary signal is Fetch Metadata: browsers send
``Sec-Fetch-Site`` with every request and pages cannot forge it. Only when it is missing
(older browsers) is ``Origin`` compared instead. A cross-site POST is refused
(``RejectedRequest``) and becomes a security event (ADR 0053).

Rate limits use slowapi, keyed by client IP (behind a proxy, uvicorn's ``--proxy-headers``
supplies the real address). slowapi buckets per request path, so links that match no booking
get a bound of their own, one bucket per address whatever the path (``count_unknown_link``).
"""

from dataclasses import dataclass

import structlog
from fastapi import Request
from limits import parse
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.domain.security_log import CROSS_SITE, ORIGIN_MISMATCH

log = structlog.get_logger()


class RejectedRequest(Exception):  # noqa: N818 - reads as an outcome, like HTTPException
    """The request is refused before it reaches the domain (cross-site POST, bot, bad input).

    Args:
        status: HTTP status of the error page.
        reason: Reason of a ``request.rejected`` security event (ADR 0053); empty when the
            refusal is ordinary bad input rather than an attempt.
    """

    def __init__(self, status: int = 400, reason: str = "") -> None:
        """Store the status and the reason."""
        super().__init__(status)
        self.status = status
        self.reason = reason


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
        log.info("request.rejected", reason=CROSS_SITE, sec_fetch_site=site[:32])
        raise RejectedRequest(403, CROSS_SITE)
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if origin is not None and origin != expected:
        # Origins are not personal data; logging them makes misconfigured proxies visible.
        log.info("request.rejected", reason=ORIGIN_MISMATCH, origin=origin[:200], expected=expected)
        raise RejectedRequest(403, ORIGIN_MISMATCH)


@dataclass
class RateLimits:
    """Current limit strings; set once per application in ``configure_rate_limits``."""

    bookings: str = "10/hour"
    reads: str = "60/minute"
    unknown_links: str = "20/hour"
    assets: str = "600/minute"
    checks: str = "6/minute"
    agent_tokens: str = "10/minute"
    agent_calls: str = "120/minute"


LIMITS = RateLimits()
limiter = Limiter(key_func=get_remote_address)


def configure_rate_limits(
    *,
    enabled: bool,
    bookings: str,
    reads: str,
    unknown_links: str = "20/hour",
    agent_tokens: str = "10/minute",
    agent_calls: str = "120/minute",
) -> None:
    """Apply the settings to the process-wide limiter and clear its counters."""
    limiter.enabled = enabled
    LIMITS.bookings = bookings
    LIMITS.reads = reads
    LIMITS.unknown_links = unknown_links
    LIMITS.agent_tokens = agent_tokens
    LIMITS.agent_calls = agent_calls
    limiter.reset()


def set_rate_limits(
    *, bookings: str, reads: str, unknown_links: str, agent_tokens: str, agent_calls: str
) -> None:
    """Apply changed limits to the next request, keeping the counters (ADR 0042 §6).

    The switch ``RATE_LIMIT_ENABLED`` is deployment-only and stays as it was.
    """
    LIMITS.bookings = bookings
    LIMITS.reads = reads
    LIMITS.unknown_links = unknown_links
    LIMITS.agent_tokens = agent_tokens
    LIMITS.agent_calls = agent_calls


def booking_limit() -> str:
    """Limit for writes: booking, cancelling, rescheduling."""
    return LIMITS.bookings


def read_limit() -> str:
    """Limit for availability pages."""
    return LIMITS.reads


def unknown_link_limit() -> str:
    """Limit for management links that match no booking, per IP address, over all paths."""
    return LIMITS.unknown_links


def count_unknown_link(request: Request) -> bool:
    """Count a management link that matched no booking against its caller's bound.

    Only answers that already are a 404 are counted, so participants with a valid link never use
    the bound up. The bucket is keyed by address alone, not by path, so guessing different
    tokens on one or many ``/b/{token}`` routes draws on the same budget (ADR 0011).

    Args:
        request: The request whose link matched nothing.

    Returns:
        Whether the caller is now over the bound; always ``False`` while rate limits are off.
    """
    if not limiter.enabled:
        return False
    item = parse(unknown_link_limit())
    return not limiter.limiter.hit(item, "unknown-management-link", get_remote_address(request))


def asset_limit() -> str:
    """Limit for branding assets (theme stylesheet, logo); cached by browsers, so generous."""
    return LIMITS.assets


def agent_token_limit() -> str:
    """Limit for the machine API's token endpoint, per IP (credential guessing, ADR 0023)."""
    return LIMITS.agent_tokens


def agent_call_limit() -> str:
    """Limit for machine API calls, per API client (ADR 0023)."""
    return LIMITS.agent_calls


def check_limit() -> str:
    """Limit for the admin "Koppeling controleren" button: each run calls the platforms."""
    return LIMITS.checks
