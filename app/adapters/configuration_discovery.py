"""Fetching a provider's issuer for a configuration document (ADR 0099 point 8).

A document names a provider by its discovery address and never carries the issuer: it is what
the provider's own discovery document says, as the portal checks it when a provider is
registered (ADR 0083 point 7). This module is that check for a document: one guarded fetch
through the ``DiscoveryFetcher`` port, the domain's ``check_discovery``, and the issuer back.
The service calls it outside the write lock.
"""

from app.domain.configuration_document_service import IssuerOf
from app.domain.errors import DomainError, ErrorCode
from app.domain.identity_providers import ClientAuth, DiscoveryRefusedError, check_discovery
from app.domain.ports import DiscoveryFetcher, IdentityError


def issuer_fetcher(fetcher: DiscoveryFetcher, *, production: bool) -> IssuerOf:
    """Return the function that fetches and checks a discovery document, then names its issuer.

    Args:
        fetcher: Fetches the discovery document through the outbound guard.
        production: Whether the issuer must be the base of the discovery address (outside
            production the announced issuer is accepted and pinned, as the portal does).

    Returns:
        A coroutine function ``(discovery_url, client_auth) -> issuer``; it raises
        ``DomainError`` ``discovery_refused`` (field ``discovery_url``) when the document
        cannot be fetched or is refused.
    """

    async def issuer_of(discovery_url: str, client_auth: str) -> str:
        try:
            document = await fetcher.fetch(discovery_url)
            return check_discovery(
                document, discovery_url, ClientAuth(client_auth), strict_issuer=production
            )
        except (IdentityError, DiscoveryRefusedError, ValueError) as refused:
            raise DomainError(ErrorCode.DISCOVERY_REFUSED, "discovery_url") from refused

    return issuer_of
