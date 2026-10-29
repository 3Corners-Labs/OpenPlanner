"""Allowed host origins for the embedded booking widget (ADR 0035 §3).

An organisation keeps a list of the websites on which its groups may be embedded. Browsers
send the page's origin (``scheme://host[:port]``) with every cross-origin request; the widget
API answers only origins on this list. The rule is therefore exact: one scheme, one host, one
port, no wildcards, no paths.

Admins paste what is in their address bar, so a trailing ``/``, upper case and the default
port are accepted and normalised to what a browser sends (``https://WWW.Gemeente.nl:443/`` →
``https://www.gemeente.nl``).

Only ``https`` is accepted. ``localhost``, ``127.0.0.1`` and ``[::1]`` (over ``http`` or
``https``) are accepted only when the caller allows it: development and test installations,
so the end-to-end host page can run on a local port.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.domain.errors import DomainError, ErrorCode

MAX_ORIGIN_LENGTH = 200
"""Longer than any real ``scheme://host:port``; keeps the column and the form bounded."""

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})
DEFAULT_PORTS = {"https": 443, "http": 80}

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"{_LABEL}(?:\.{_LABEL})*")
_PORT = re.compile(r"[0-9]{1,5}")


@dataclass(frozen=True)
class AllowedOrigins:
    """The normalised origins of one organisation, in the order the admin entered them."""

    origins: tuple[str, ...] = ()

    def allows(self, origin: str | None) -> bool:
        """Return whether a request's ``Origin`` header is exactly on the list."""
        return origin is not None and origin in self.origins


def normalise_origin(text: str, *, allow_local_http: bool) -> str | None:
    """Return ``text`` as the origin a browser would send, or ``None`` if it is not one.

    Args:
        text: One origin as an admin typed it.
        allow_local_http: Whether ``localhost``, ``127.0.0.1`` and ``[::1]`` are accepted.
    """
    text = text.strip()
    if not text or len(text) > MAX_ORIGIN_LENGTH or "*" in text or "\\" in text:
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    raw_netloc = parts.netloc.lower()
    host = raw_netloc.rsplit(":", 1)[0] if not raw_netloc.endswith("]") else raw_netloc
    if parts.path not in ("", "/") or parts.query or parts.fragment or "@" in raw_netloc:
        return None
    if ":" in raw_netloc and not raw_netloc.endswith("]"):
        # urlsplit accepts "host:" and "host:+1"; only plain digits are a port
        if not _PORT.fullmatch(raw_netloc.rsplit(":", 1)[1]):
            return None
    if port == 0:
        return None
    if host in LOCAL_HOSTS:
        if not (allow_local_http and scheme in DEFAULT_PORTS):
            return None
    elif scheme != "https" or not _HOST.fullmatch(host):
        return None
    if port is None or port == DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def parse_origins(text: str, *, allow_local_http: bool, limit: int) -> AllowedOrigins:
    """Return the origins of a textarea, one per line; blank lines and repeats are skipped.

    Raises:
        DomainError: ``invalid_configuration`` with field ``origin_{n}`` for the first line
            ``n`` (1-based, counting every line) that is not an origin, or field
            ``origins_count`` when more than ``limit`` remain.
    """
    origins: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        origin = normalise_origin(line, allow_local_http=allow_local_http)
        if origin is None:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, f"origin_{number}")
        if origin not in origins:
            origins.append(origin)
    if len(origins) > limit:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "origins_count")
    return AllowedOrigins(tuple(origins))
