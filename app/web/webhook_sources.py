"""The settings and the request as the source-network rule needs them (ADR 0124 point 3).

The rule itself is pure (``app.domain.webhook_sources``); this is where the web layer gathers its
inputs from the running application: the installation's default networks per platform, whether the
host demands a list, and what is known of the proxy chain. The gate and the admin screens read them
from here, so both say the same about an address.

An installation default that cannot be read is an unreadable list, never an empty one (which would
mean *any source*): either its value does not parse, or the portal holds a value that the rules
refused and so ignores (the same value would otherwise fall back to the empty default silently).
"""

from starlette.requests import Request

from app.config import Settings
from app.domain.errors import DomainError
from app.domain.networks import NetworkList
from app.domain.source_networks import source_networks_of
from app.domain.webhook_sources import ProxyChain
from app.web.caller import forwarded_headers_ignored

VIDEO_KEY = "webhook_source_networks_video"
CONTACT_CENTER_KEY = "webhook_source_networks_contact_center"


def _stored_value_is_ignored(request: Request, key: str) -> bool:
    """Return whether the settings ignore a stored portal value for ``key`` (``LiveSettings``).

    ``app.state.live_settings`` is set at start-up in every running application (``app.main``),
    so a missing one is a defect that must fail loudly, not read as "nothing stored".
    """
    ignored: bool = request.app.state.live_settings.portal_ignored(key)
    return ignored


def _default(request: Request, key: str) -> NetworkList:
    """Return the installation's default list under ``key``; unreadable when it cannot be read."""
    settings: Settings = request.app.state.settings
    try:
        networks = source_networks_of(getattr(settings, key))
    except DomainError:
        return NetworkList(unreadable=True)
    if _stored_value_is_ignored(request, key):
        return NetworkList(unreadable=True)
    return NetworkList(networks)


def video_networks(request: Request) -> NetworkList:
    """Return the installation's allowed networks for the video platform's webhooks."""
    return _default(request, VIDEO_KEY)


def contact_center_networks(request: Request) -> NetworkList:
    """Return the installation's default networks for contact-center webhooks."""
    return _default(request, CONTACT_CENTER_KEY)


def proxy_chain(request: Request) -> ProxyChain:
    """Return what is known about the path from the caller to this process.

    Built from ``FORWARDED_ALLOW_IPS``, the address webhooks are registered at and whether this
    call's own forwarded headers were ignored (``app.web.caller``, the one reader of them). The
    proxy watch's standing sighting (ADR 0086) is deliberately not an input: it is a finding on
    System status, and letting it refuse calls would let a stranger switch every list off.
    """
    settings: Settings = request.app.state.settings
    return ProxyChain.of(
        forwarded_allow_ips=settings.forwarded_allow_ips,
        tls_proxy_bundled=settings.tls_proxy_bundled,
        webhook_base_url=settings.webhook_base,
        headers_ignored=forwarded_headers_ignored(request),
    )
