"""The hop between a reverse proxy and the web container (ADR 0091 point 3).

The bundled proxy reaches web over the ``edge`` network of one Docker host; that hop stays plain
HTTP as a recorded exception. An own proxy on another machine crosses a real network, so web
should serve TLS there. Pure Python: the settings arrive as plain values.
"""

BUNDLED_PROXY_ADDRESS = "172.31.240.2"
"""The fixed address of the bundled proxy on the ``edge`` network (``docker-compose.yml``)."""


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
