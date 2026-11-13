"""Request protection for HTML forms and audited downloads: same-origin checks and rate limits.

Forms carry no CSRF token. The primary signal is Fetch Metadata: browsers send
``Sec-Fetch-Site`` with every request and pages cannot forge it. Only when it is missing
(older browsers) is ``Origin`` compared instead. A cross-site POST, or a cross-site GET of an
audited download, is refused (``RejectedRequest``) and becomes a security event (ADR 0053); an
audited download that the browser loads in advance (prefetch, prerender) is refused without one.

Rate limits use slowapi, keyed by caller (``caller_of``): the client IP as uvicorn derived it
(behind a proxy, ``--proxy-headers`` supplies the real address), an IPv6 caller by its /64 so
that rotating addresses within it never gives a fresh bucket (ADR 0124 step 2a). slowapi
buckets per request path, so links that match no booking get a bound of their own, one bucket
per address whatever the path (``count_unknown_link``).
"""

from dataclasses import dataclass, fields
from urllib.parse import urlsplit

import structlog
from fastapi import Request
from limits import parse
from slowapi import Limiter

from app.config import Settings
from app.domain.security_log import CROSS_SITE, ORIGIN_MISMATCH
from app.web.caller import caller_of
from app.web.route_dependencies import dependency_calls

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


def _speculative(request: Request) -> bool:
    """Return whether the browser loads the address ahead of the person: prefetch or prerender.

    ``Sec-Purpose`` in current browsers, ``Purpose`` in older ones. Such a load may follow no
    click at all: a hover over a bookmark, an address the address bar predicts. The person's
    own request comes after it, without the header.
    """
    return any(request.headers.get(name) for name in ("sec-purpose", "purpose"))


def from_this_page(request: Request) -> bool:
    """Return whether ``request`` comes from the very page it asks for, by the person's action.

    For a ``GET`` that shows personal data and is audited (the participant's contact details,
    ADR 0044 §2): only the button on that page may make it. The browser must not mark the
    request as coming from elsewhere (``Sec-Fetch-Site`` other than ``same-origin``: typed,
    bookmarked, shared in a mail or chat, another site), it must not be a prefetch or
    prerender (``Sec-Purpose``, ``Purpose``), and its ``Referer`` must be this page's own
    path, which the portal's ``same-origin`` referrer policy sends. So a shared address, or a
    link to it planted on any other page of the portal, neither shows the data nor writes a
    request in someone else's name. The browser vouches for the origin with ``Sec-Fetch-Site``,
    so behind a proxy only the path is compared; an older browser without it must send this
    site's own origin in the ``Referer`` as well. Without a ``Referer`` (stripped by a proxy or
    an extension) nothing is shown: it fails closed.
    """
    if _speculative(request):
        return False
    referer = urlsplit(request.headers.get("referer", ""))
    if referer.path != request.url.path:
        return False
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        return site == "same-origin"
    return (referer.scheme, referer.netloc) == (request.url.scheme, request.url.netloc)


def require_same_origin(request: Request) -> None:
    """FastAPI dependency: refuse a request that a browser marks as cross-site.

    Every form post depends on it, and so do the audited downloads, the admin ``GET`` routes
    that write an audit event when they are opened (ASVS V3.5.3). ``none`` passes: typed,
    bookmarked or opened from another program, a navigation the person made themselves. A
    request the browser loads in advance (prefetch or prerender, which browsers do for a ``GET``
    only, such as an audited download) is refused as well, without a security event: it may
    follow no click, and the click asks again. A ``GET`` that shows personal data asks more
    (``from_this_page``): only its own page counts.

    ``Origin`` is not trusted when ``Sec-Fetch-Site`` is present: some referrer policies make
    browsers send ``Origin: null`` even for same-origin form posts.

    Raises:
        RejectedRequest: 403 if ``Sec-Fetch-Site`` is not same-origin/none, or, without it,
            if ``Origin`` differs from this site; 403 without a reason for a request loaded in
            advance.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        log.info("request.rejected", reason=CROSS_SITE, sec_fetch_site=site[:32])
        raise RejectedRequest(403, CROSS_SITE)
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if site is None and origin is not None and origin != expected:
        # Origins are not personal data; logging them makes misconfigured proxies visible.
        log.info("request.rejected", reason=ORIGIN_MISMATCH, origin=origin[:200], expected=expected)
        raise RejectedRequest(403, ORIGIN_MISMATCH)
    if _speculative(request):
        raise RejectedRequest(403)


def is_action(request: Request) -> bool:
    """Return whether the request's route is an action: it depends on ``require_same_origin``.

    Every form post is, and so is a ``GET`` with an effect, such as an audited download
    (ASVS V3.5.3). Nothing may lead back to an action by itself: a login returns to the page
    that asked for it (``app.admin.return_to``), and a page served at a ``GET`` action's
    address gets language links to the overview (``app.web.templates.switch_href``).
    """
    return require_same_origin in dependency_calls(request.scope.get("route"))


@dataclass
class RateLimits:
    """Current limit strings; set once per application in ``configure_rate_limits``.

    A new limit is one field here and one line in ``from_settings``.
    """

    bookings: str = "10/hour"
    reads: str = "60/minute"
    unknown_links: str = "20/hour"
    assets: str = "600/minute"
    checks: str = "6/minute"
    agent_tokens: str = "10/minute"
    agent_calls: str = "120/minute"
    reports: str = "30/minute"
    change_requests: str = "20/hour"
    webhooks: str = "600/minute"

    @classmethod
    def from_settings(cls, settings: Settings) -> "RateLimits":
        """Return the limits the settings ask for; the one place that reads them."""
        return cls(
            bookings=settings.rate_limit_bookings,
            reads=settings.rate_limit_reads,
            unknown_links=settings.rate_limit_unknown_links,
            agent_tokens=settings.rate_limit_agent_tokens,
            agent_calls=settings.rate_limit_agent_calls,
            reports=f"{settings.reporting_api_rate_per_minute}/minute",
            change_requests=f"{settings.agent_change_requests_per_hour}/hour",
            webhooks=f"{settings.rate_limit_webhook_per_minute}/minute",
        )


LIMITS = RateLimits()
limiter = Limiter(key_func=caller_of)


def _adopt(limits: RateLimits) -> None:
    """Make ``limits`` the current ones, in place: the accessors read ``LIMITS``."""
    for field in fields(limits):
        setattr(LIMITS, field.name, getattr(limits, field.name))


def configure_rate_limits(limits: RateLimits, *, enabled: bool) -> None:
    """Apply the limits to the process-wide limiter and clear its counters."""
    limiter.enabled = enabled
    _adopt(limits)
    limiter.reset()


def set_rate_limits(limits: RateLimits) -> None:
    """Apply changed limits to the next request, keeping the counters (ADR 0042 §6).

    The switch ``RATE_LIMIT_ENABLED`` is deployment-only and stays as it was.
    """
    _adopt(limits)


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
    return not limiter.limiter.hit(item, "unknown-management-link", caller_of(request))


def asset_limit() -> str:
    """Limit for branding assets (theme stylesheet, logo); cached by browsers, so generous."""
    return LIMITS.assets


def agent_token_limit() -> str:
    """Limit for the machine API's token endpoint, per IP (credential guessing, ADR 0023)."""
    return LIMITS.agent_tokens


def agent_call_limit() -> str:
    """Limit for machine API calls, per API client (ADR 0023)."""
    return LIMITS.agent_calls


def reports_limit() -> str:
    """Limit for the usage API, per API client (ADR 0117 point 13)."""
    return LIMITS.reports


def change_request_limit() -> str:
    """Limit for a bot's change requests, per API client (ADR 0023 step 2)."""
    return LIMITS.change_requests


def webhook_limit() -> str:
    """Limit for inbound webhooks, per platform, organisation and source address (ADR 0124)."""
    return LIMITS.webhooks


def webhook_retry_after() -> str:
    """Return the ``Retry-After`` seconds of a refused webhook: the window of its limit.

    Derived from the limit string, so it holds when the limit is no longer per minute.
    """
    return str(parse(webhook_limit()).get_expiry())


def _within(bucket: str, platform: str, organisation: str, source: str) -> bool:
    """Count one call against ``bucket`` of one platform, organisation and caller address."""
    if not limiter.enabled:
        return True
    return limiter.limiter.hit(parse(webhook_limit()), bucket, platform, organisation, source)


def within_webhook_limit(platform: str, organisation: str, source: str) -> bool:
    """Count one verified webhook against its bucket and return whether it may go on.

    The bucket is the platform, the organisation (``-`` for an address of the installation)
    and the caller's address, so organisations behind one supplier address do not share one
    (ADR 0124 point 5). It counts verified calls only: the gate calls it after the signature
    held, so a caller that cannot sign never uses it up. It is held by the common limiter: the
    off switch and the reset work as for every other limit.

    Args:
        platform: The platform's source code, e.g. ``webex``.
        organisation: The organisation's slug, or ``-``.
        source: The caller's address, as the server derived it.

    Returns:
        ``False`` when the caller is over the limit; always ``True`` while rate limits are off.
    """
    return _within("webhook", platform, organisation, source)


def within_refused_limit(platform: str, organisation: str, source: str) -> bool:
    """Count one refused webhook against its own bucket and return whether it keeps its reason.

    A bucket apart from the verified one, with the same value and key: calls that failed a
    check (content type, size, signature, time, shape) count here and nowhere else, so
    unverified calls from a shared sending address can never use up the budget of the authentic
    deliveries (ADR 0124 point 5). Over it, the caller is told ``rate`` instead of the reason.

    Args:
        platform: The platform's source code.
        organisation: The organisation's slug, or ``-``.
        source: The caller's address, as the server derived it.
    """
    return _within("webhook-refused", platform, organisation, source)


def within_unknown_address_limit(source: str) -> bool:
    """Count one call to an address that does not exist against its caller's own bucket.

    The bucket only decides, for calls to addresses that do not exist, between a 404 and a 429
    and so caps how many refusal events a prober can start; it never touches a call to an
    address that exists, so a customer of the same supplier cannot lock real organisations out
    from a shared address (ADR 0124 point 5). The key is the caller's address alone, with the
    value of ``rate_limit_webhook_per_minute``: the platform and organisation in the path come
    from the caller, so they never make a key.

    Args:
        source: The caller's address, as the server derived it.

    Returns:
        ``False`` when the caller is over the bucket; always ``True`` while rate limits are off.
    """
    if not limiter.enabled:
        return True
    return limiter.limiter.hit(parse(webhook_limit()), "webhook-unknown", source)


def check_limit() -> str:
    """Limit for the admin "Koppeling controleren" button: each run calls the platforms."""
    return LIMITS.checks
