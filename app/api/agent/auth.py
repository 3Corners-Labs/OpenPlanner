"""The one way into machine API routes: ``RequireScope`` (ADR 0023).

Every route except the token endpoint and the documentation depends on exactly one
``RequireScope``. It reads the bearer token, lets ``ApiAccessService`` authenticate it (switches,
expiry, client enabled, network) and checks the scope through the matrix, on the route's group
when it has one. The dependency object is inspectable, so a test can prove that no route lacks
it, as for admin routes (``RequirePermission``).
"""

from dataclasses import dataclass
from typing import Annotated

import structlog
from fastapi import Depends, Request
from fastapi.openapi.models import OAuthFlowClientCredentials, OAuthFlows
from fastapi.security import OAuth2

from app.api.agent.errors import ApiProblem
from app.config import AGENT_API_PREFIX
from app.domain.api_access import ApiAccessService
from app.domain.api_clients import ApiAuthError, ApiCaller
from app.domain.authorization import API_SCOPES, ApiScope
from app.domain.scheduling import GroupSummary
from app.domain.security_log import TOKEN_REJECTED, UNKNOWN_TOKEN
from app.web.security_events import report

log = structlog.get_logger()

TOKEN_PATH = f"{AGENT_API_PREFIX}/oauth/token"

SCOPE_DESCRIPTIONS: dict[ApiScope, str] = {
    ApiScope.AVAILABILITY_READ: "Read groups and their free appointment times (no personal data).",
}
"""Shown in the OpenAPI spec; the admin portal has its own translated labels."""

oauth2 = OAuth2(
    flows=OAuthFlows(
        clientCredentials=OAuthFlowClientCredentials(
            tokenUrl=TOKEN_PATH, scopes={s.value: SCOPE_DESCRIPTIONS[s] for s in API_SCOPES}
        )
    ),
    scheme_name="clientCredentials",
    auto_error=False,
)
"""Declares the security scheme in the spec; the token itself is checked by ``RequireScope``."""


def api_access(request: Request) -> ApiAccessService:
    """Return the application's machine API service."""
    service: ApiAccessService = request.app.state.api_access
    return service


def client_address(request: Request) -> str | None:
    """Return the caller's IP address (behind a proxy, uvicorn's ``--proxy-headers`` sets it)."""
    return request.client.host if request.client else None


@dataclass(frozen=True)
class AgentContext:
    """What a machine API route gets: the caller and, for group routes, the group."""

    caller: ApiCaller
    group: GroupSummary | None = None


def bearer_token(request: Request) -> str:
    """Return the bearer token of ``request``.

    Raises:
        ApiProblem: ``invalid_token`` without an ``Authorization: Bearer`` header.
    """
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token or len(token) > 256:
        raise ApiProblem("invalid_token")
    return token


class RequireScope:
    """FastAPI dependency: allow the request only for a valid token holding ``scope``.

    Args:
        scope: The one scope this route needs, or ``None`` for routes any valid token may
            use (``/me``).
        group_param: Name of the path parameter with the group slug; the scope is then
            checked on that group (organisation and the client's groups).
    """

    def __init__(self, scope: ApiScope | None, group_param: str | None = None) -> None:
        """Store the declaration; it is also read by the route-coverage test."""
        self.scope = scope
        self.group_param = group_param

    async def __call__(
        self, request: Request, _: Annotated[str | None, Depends(oauth2)] = None
    ) -> AgentContext:
        """Authenticate and authorise (the ``oauth2`` parameter only documents the scheme).

        Raises:
            ApiProblem: ``invalid_token`` or ``insufficient_scope``.
            ApiAuthError: ``invalid_token`` or ``temporarily_unavailable`` from the service.
            DomainError: ``group_not_found``.
        """
        token = bearer_token(request)
        service = api_access(request)
        try:
            caller = await service.authenticate(token, client_address(request))
        except ApiAuthError as error:
            log.info("agent_api.refused", reason=error.reason)
            if error.reason == UNKNOWN_TOKEN:  # a guess: no client to audit it under (ADR 0053)
                await report(request, TOKEN_REJECTED, UNKNOWN_TOKEN)
            raise
        request.state.agent_client_id = caller.client.client_id
        group = None
        if self.scope is not None:
            try:
                if self.group_param is not None:
                    slug = str(request.path_params[self.group_param])
                    group = await service.authorize(caller, self.scope, slug)
                else:
                    await service.require_scope(caller, self.scope)
            except Exception:
                log.info(
                    "agent_api.decision",
                    client=caller.client.client_id,
                    scope=self.scope.value,
                    allowed=False,
                )
                raise
        log.info(
            "agent_api.decision",
            client=caller.client.client_id,
            scope=self.scope.value if self.scope else "",
            allowed=True,
        )
        return AgentContext(caller, group)


def client_key(request: Request) -> str:
    """Rate-limit key of an authenticated call: the client id (set by ``RequireScope``)."""
    client_id = getattr(request.state, "agent_client_id", None)
    return f"agent:{client_id}" if client_id else f"agent-ip:{client_address(request)}"
