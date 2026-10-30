"""``MailDomainResolver`` on ``dnspython``, through the host's own resolver (ADR 0073).

One TXT lookup per name, no search list, a fixed lifetime. The resolver configuration
(``/etc/resolv.conf``) is read on first use, so an installation without one still starts and
only the check reports *unknown*. A name without records is an empty answer; a lookup that
fails (timeout, no name servers, no configuration) is ``None``, logged with the name (as
``domain``; the key ``name`` would be masked as personal data) and the error class only. The
names are the organisation's sender domain, never personal data.
"""

from typing import Any

import dns.asyncresolver
import dns.exception
import dns.resolver
import structlog

log = structlog.get_logger()

LIFETIME_SECONDS = 5.0
"""How long one lookup may take in total, retries included; fixed (ADR 0073, *Fixed on purpose*)."""


class DnsTxtResolver:
    """Looks up TXT records with the host's resolver.

    Args:
        lifetime: Seconds per lookup.
        resolver: An explicit ``dns.asyncresolver.Resolver`` (tests, a private resolver);
            defaults to the host's configuration, read on first use.
    """

    def __init__(self, lifetime: float = LIFETIME_SECONDS, resolver: Any = None) -> None:
        """Store the options; the host's resolver is created lazily."""
        self._lifetime = lifetime
        self._resolver = resolver

    async def txt_records(self, name: str) -> tuple[str, ...] | None:
        """Return the TXT records of ``name``; ``()`` when it has none, ``None`` on failure."""
        try:
            if self._resolver is None:
                self._resolver = dns.asyncresolver.Resolver()
            answer = await self._resolver.resolve(
                name, "TXT", lifetime=self._lifetime, search=False
            )
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return ()
        except dns.exception.DNSException as exc:
            log.warning("dns.lookup_failed", domain=name, error=type(exc).__name__)
            return None
        return tuple(b"".join(record.strings).decode("utf-8", "replace") for record in answer)
