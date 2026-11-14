"""The hops inside one Docker host that stay plain HTTP, as recorded exceptions (ADR 0091).

These hops are meant never to leave the host, so they run without TLS, as recorded exceptions:

* the bundled proxy to web, over the ``edge`` network (ADR 0091 point 2). An own proxy on
  another machine crosses a real network, so web should serve TLS there (point 3);
* to the bundled identity provider (ADR 0125): web and worker over the compose network (the
  back channel: discovery, token, keys, the staff directory), and the proxy over ``edge`` (the
  browser's ``/idp`` route, whose leg from the browser is https). Until ADR 0125 step 2 gives
  the provider a fixed address, both reach it by the name ``idp``, which the host's resolver
  may answer while the container is down.

Pure Python: the settings arrive as plain values.
"""

from app.domain.identity_providers import DISCOVERY_SUFFIX

BUNDLED_PROXY_ADDRESS = "172.31.240.2"
"""The fixed address of the bundled proxy on the ``edge`` network (``docker-compose.yml``)."""

BUNDLED_REALM_PATH = "/realms/openplanner/"
"""Where the bundled provider's realm sits in its addresses (ADR 0090, the realm template)."""

BUNDLED_IDP_DISCOVERY_URL = f"http://idp:8080/idp{BUNDLED_REALM_PATH.rstrip('/')}{DISCOVERY_SUFFIX}"
"""Where web reads the bundled provider's login on the compose network (ADR 0112 point 4).

The compose service ``idp`` on its fixed port, under the proxy's ``/idp`` path, in the bundled
realm: what ``scripts/render-keycloak-realm.sh`` writes for an https public address.
"""


def proxy_hop_unencrypted(forwarded_allow_ips: str | None, *, web_tls: bool) -> bool:
    """Return whether web trusts a proxy other than the bundled one and serves no TLS.

    ``FORWARDED_ALLOW_IPS`` names the proxies uvicorn trusts. Anything other than the bundled
    proxy's address (nothing at all means the bundled proxy) points to a proxy that is probably
    on another machine, so its traffic runs unencrypted unless web serves TLS.

    Args:
        forwarded_allow_ips: The raw comma separated value, or ``None`` when unset.
        web_tls: Whether web serves HTTPS (``WEB_TLS_CERT_FILE`` and ``WEB_TLS_KEY_FILE``).

    Returns:
        ``True`` when System status should warn.
    """
    if web_tls:
        return False
    trusted = {part.strip() for part in (forwarded_allow_ips or "").split(",") if part.strip()}
    return bool(trusted - {BUNDLED_PROXY_ADDRESS})


def bundled_idp_hop(discovery_url: str, issuer: str | None) -> bool:
    """Return whether a plain-http login is the bundled provider's internal hop (ADR 0125).

    Only the exact address the render script writes counts, and only with an https issuer.
    The genuine provider derives the issuer and the endpoints it sends browsers to from one
    host name (``KC_HOSTNAME``), so with an https issuer the browser reaches the proxy over
    https; the proxy's own leg to the provider is the other hop of ADR 0125. Any other http
    discovery URL, or this one with an http or derived issuer, is not the exception.

    Args:
        discovery_url: ``OIDC_DISCOVERY_URL``.
        issuer: ``OIDC_ISSUER``; ``None`` when the issuer is derived from the discovery URL.

    Returns:
        ``True`` when production may accept the plain-http discovery URL.
    """
    return discovery_url == BUNDLED_IDP_DISCOVERY_URL and (issuer or "").startswith("https://")
