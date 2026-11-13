"""The settings and the proxy watch as the source-network rule needs them (ADR 0124 point 3).

The rule itself is pure (``app.domain.webhook_sources``); this is where the web layer gathers its
inputs from the running application: the installation's default networks per platform, whether the
host demands a list, and what is known of the proxy chain. The gate and the admin screens read them
from here, so both say the same about an address.
"""

from starlette.requests import Request

from app.config import Settings
from app.domain.webhook_sources import ProxyChain, networks_of
from app.web.caller import forwarded_headers_ignored


def video_networks(settings: Settings) -> tuple[str, ...]:
    """Return the installation's allowed networks for the video platform's webhooks."""
    return networks_of(settings.webhook_source_networks_video)


def contact_center_networks(settings: Settings) -> tuple[str, ...]:
    """Return the installation's default networks for contact-center webhooks."""
    return networks_of(settings.webhook_source_networks_contact_center)


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
