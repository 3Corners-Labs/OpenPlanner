"""The one way into admin routes: ``RequirePermission`` (ADR 0014).

Every admin route depends on exactly one ``RequirePermission``. It resolves the logged-in
principal, asks ``AdminService.authorize`` (which audits denials), logs the decision and either
returns an ``AdminContext`` or raises ``LoginRequired`` / ``AccessDenied``. The dependency
object is inspectable, so a test can prove that no admin route lacks one.

Templates receive ``can(permission, group=None)`` to show only the actions the matrix allows;
hiding is a convenience, the server always decides again.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

import structlog
from fastapi import Request

from app.admin.session import current_principal
from app.domain.admin import AdminService, Authorization
from app.domain.authorization import (
    LEVELS,
    Level,
    Permission,
    Principal,
    Target,
    decide,
    scope_for,
)
from app.domain.ports import Clock
from app.domain.scheduling import GroupSummary

log = structlog.get_logger()


class LoginRequired(Exception):  # noqa: N818 - control flow, rendered as a redirect
    """No (valid) admin session; the visitor is sent to the login."""


class AccessDenied(Exception):  # noqa: N818 - control flow, rendered as a 403 page
    """The matrix refused the request."""


@dataclass(frozen=True)
class AdminContext:
    """What an admin route gets: the principal, the decision and the group, if any."""

    principal: Principal
    authorization: Authorization

    @property
    def group(self) -> GroupSummary:
        """The group of a group-level route."""
        if self.authorization.group is None:  # pragma: no cover - programming error
            raise LookupError("route has no group")
        return self.authorization.group


def admin_service(request: Request) -> AdminService:
    """Return the application's admin service."""
    service: AdminService = request.app.state.admin_service
    return service


def session_principal(request: Request) -> Principal | None:
    """Return the logged-in principal of ``request``, honouring the absolute lifetime."""
    clock: Clock = request.app.state.clock
    hours: int = request.app.state.settings.admin_session_hours
    return current_principal(request, clock.now(), timedelta(hours=hours))


def can_function(principal: Principal) -> Callable[..., bool]:
    """Return ``can(permission, group=None)`` for templates (same matrix as the server)."""

    def can(permission: Permission, group: GroupSummary | None = None) -> bool:
        level = LEVELS[permission]
        if level is Level.GLOBAL:
            return decide(principal, permission, Target.everything()).allowed
        if group is not None and level is Level.GROUP:
            target = Target.for_group(group.group.slug, group.organisation_slug)
            return decide(principal, permission, target).allowed
        return not scope_for(principal, permission).is_empty

    return can


class RequirePermission:
    """FastAPI dependency: allow the request only if the matrix grants ``permission``.

    Args:
        permission: The one permission this route needs.
        group_param: Name of the path parameter with the group slug (group-level routes).
            Without it, a non-global permission must be held for at least one target.
    """

    def __init__(self, permission: Permission, group_param: str | None = None) -> None:
        """Store the declaration; it is also read by the route-coverage test."""
        self.permission = permission
        self.group_param = group_param

    async def __call__(self, request: Request) -> AdminContext:
        """Resolve the principal and decide.

        Raises:
            LoginRequired: Without a valid session.
            AccessDenied: When the matrix refuses.
        """
        principal = session_principal(request)
        if principal is None:
            raise LoginRequired
        service = admin_service(request)
        if self.group_param is not None:
            slug = str(request.path_params[self.group_param])
            authorization = await service.authorize(principal, self.permission, group_slug=slug)
        else:
            authorization = await service.authorize_anywhere(principal, self.permission)
        log.info(
            "authz.decision",
            actor=principal.subject,
            permission=self.permission.value,
            target_group=authorization.target.group,
            target_organisation=authorization.target.organisation,
            allowed=authorization.decision.allowed,
            reason=authorization.decision.reason.value,
        )
        if not authorization.decision.allowed:
            raise AccessDenied
        return AdminContext(principal, authorization)
