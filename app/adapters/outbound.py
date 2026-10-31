"""The outbound guard for addresses an admin types in the portal (ADR 0083 point 8).

Fixed or environment-given URLs (Webex, the platform identity provider) do not need it. A URL
from the portal does: without a guard the server would fetch whatever an admin names, such as
a cloud metadata service or an internal host (server-side request forgery, OWASP A10:2021).
This follows the OWASP SSRF Prevention Cheat Sheet:

    guarded_request(url) ─► https only, no user:password@ ─► resolve the name
        ─► every address public? ─► connect to that address (Host and TLS SNI keep the name,
           so the certificate is still checked against it; a DNS answer that changes between
           check and connect cannot redirect the request) ─► no redirects ─► size cap

``allow_private`` relaxes the scheme and address checks for development and test, where the
lab identity provider is an internal host; production never passes it.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Callable
from typing import Any

import httpx

MAX_RESPONSE_BYTES = 1_000_000
"""Largest response read; a discovery document or key set is a few kilobytes."""

FETCH_TIMEOUT_SECONDS = 10.0
"""Connect and read timeout of one guarded request."""

_DECODED_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})
"""Headers that describe the encoded body; the returned body is already decoded."""

Resolver = Callable[[str, int], list[str]]
"""Returns the addresses a host name resolves to; called in a worker thread."""


class OutboundRefusedError(Exception):
    """The guard refused a request; ``reason`` says why.

    Args:
        reason: ``not_https``, ``no_host``, ``credentials_in_url``, ``unresolvable``,
            ``not_public``, ``redirect`` or ``too_large``.
    """

    def __init__(self, reason: str) -> None:
        """Store the reason; it is also the message (never the URL)."""
        super().__init__(reason)
        self.reason = reason


def is_public(address: str) -> bool:
    """Return whether ``address`` is reachable on the public internet only.

    Loopback, private, link-local (cloud metadata), carrier-grade NAT, unspecified, reserved
    and multicast addresses are not, nor IPv6 addresses that map onto such an IPv4 address.
    """
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def system_resolver(host: str, port: int) -> list[str]:
    """Return the addresses of ``host`` from the system resolver."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return []
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


async def guarded_request(
    http: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    resolve: Resolver = system_resolver,
    allow_private: bool = False,
    **kwargs: Any,
) -> httpx.Response:
    """Send one request to a portal-given ``url`` through the guard and return the response.

    ``kwargs`` go to httpx (``data``, ``headers``, ``auth``, …). The body is read in full,
    up to ``MAX_RESPONSE_BYTES``.

    Raises:
        OutboundRefusedError: Before anything is sent when the URL or its addresses fail the
            checks; after the response when it is a redirect or too large.
        httpx.HTTPError: The connection or the request failed.
    """
    target = httpx.URL(url)
    if target.scheme != "https" and not (allow_private and target.scheme == "http"):
        raise OutboundRefusedError("not_https")
    if not target.host:
        raise OutboundRefusedError("no_host")
    if target.userinfo:
        raise OutboundRefusedError("credentials_in_url")
    port = target.port or (443 if target.scheme == "https" else 80)
    addresses = await asyncio.to_thread(resolve, target.host, port)
    if not addresses:
        raise OutboundRefusedError("unresolvable")
    if not allow_private and not all(is_public(a) for a in addresses):
        raise OutboundRefusedError("not_public")
    host_header = target.host if target.port is None else f"{target.host}:{target.port}"
    headers = {**kwargs.pop("headers", {}), "host": host_header}
    extensions = {"sni_hostname": target.host} if target.scheme == "https" else {}
    request = http.build_request(
        method,
        target.copy_with(host=addresses[0]),
        headers=headers,
        extensions=extensions,
        timeout=FETCH_TIMEOUT_SECONDS,
        **kwargs,
    )
    response = await http.send(request, stream=True, follow_redirects=False)
    try:
        if response.is_redirect:
            raise OutboundRefusedError("redirect")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > MAX_RESPONSE_BYTES:
                raise OutboundRefusedError("too_large")
    finally:
        await response.aclose()
    kept = [(k, v) for k, v in response.headers.items() if k not in _DECODED_HEADERS]
    return httpx.Response(response.status_code, headers=kept, content=bytes(body), request=request)
