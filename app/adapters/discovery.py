"""Fetching a provider's discovery document for the registration check (ADR 0083 point 7).

The ``DiscoveryFetcher`` port: one guarded GET (point 8), the body read as JSON. What the
document must contain is checked by the domain (``check_discovery``); this module only
fetches and refuses what is not a JSON object.
"""

from collections.abc import Mapping

import httpx
import structlog

from app.adapters.outbound import OutboundRefusedError, Resolver, guarded_request, system_resolver
from app.domain.ports import IdentityError

log = structlog.get_logger()


class GuardedDiscoveryFetcher:
    """``DiscoveryFetcher`` through ``guarded_request``.

    Args:
        http: Shared HTTP client.
        allow_private: Relax the guard for a lab provider (development and test only).
        resolve: The name resolver (the system's; tests inject one).
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        allow_private: bool = False,
        resolve: Resolver = system_resolver,
    ) -> None:
        """Store the client and the guard's mode."""
        self._http = http
        self._allow_private = allow_private
        self._resolve = resolve

    async def fetch(self, url: str) -> Mapping[str, object]:
        """Return the JSON object at ``url``.

        Raises:
            IdentityError: The guard's reason, or ``discovery_failed``.
        """
        try:
            response = await guarded_request(
                self._http, "GET", url, resolve=self._resolve, allow_private=self._allow_private
            )
            response.raise_for_status()
            document = response.json()
        except OutboundRefusedError as refused:
            log.warning("idp.discovery_refused", reason=refused.reason)
            raise IdentityError(refused.reason) from refused
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("idp.discovery_failed", error=type(exc).__name__)
            raise IdentityError("discovery_failed") from exc
        if not isinstance(document, dict):
            raise IdentityError("discovery_failed")
        return document
