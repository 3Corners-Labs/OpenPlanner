"""The one way into admin routes: ``RequirePermission`` (ADR 0014).

Every admin route depends on exactly one ``RequirePermission``. It resolves the logged-in
principal, asks ``AdminService.authorize`` (which audits denials), logs the decision and either
returns an ``AdminContext`` or raises ``LoginRequired`` / ``AccessDenied``. The dependency
object is inspectable, so a test can prove that no admin route lacks one.

Templates receive ``can(permission, group=None)`` to show only the actions the matrix allows;
hiding is a convenience, the server always decides again.

The principal is resolved once per request by ``load_principal`` (a dependency of every admin
router): the session cookie gives the built-in roles and the other role claim values, the
role catalogue adds the custom roles those values grant now (ADR 0039), so a changed or
deleted custom role takes effect on the next request without a new login.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

import structlog
from fastapi import Request

from app.admin.session import current_principal, login_age
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
from app.domain.roles import RoleService
from app.domain.scheduling import GroupSummary

log = structlog.get_logger()


class LoginRequired(Exception):  # noqa: N818 - control flow, rendered as a redirect
    """No (valid) admin session; the visitor is sent to the login."""


class AccessDenied(Exception):  # noqa: N818 - control flow, rendered as a 403 page
    """The matrix refused the request."""


class ReauthRequired(Exception):  # noqa: N818 - control flow, rendered as a redirect
    """A change needs a recent login; the admin is sent to the IdP and back to ``next_path``.

    Args:
        next_path: The page to return to (a form page, never a POST target).
    """

    def __init__(self, next_path: str) -> None:
        """Store where to return after the fresh login."""
        super().__init__(next_path)
        self.next_path = next_path


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


def role_service(request: Request) -> RoleService:
    """Return the application's role service (custom roles, ADR 0039)."""
    service: RoleService = request.app.state.role_service
    return service


def _cookie_principal(request: Request) -> Principal | None:
    """Return the principal stored in the session, honouring the absolute lifetime."""
    clock: Clock = request.app.state.clock
    hours: int = request.app.state.settings.admin_session_hours
    return current_principal(request, clock.now(), timedelta(hours=hours))


async def load_principal(request: Request) -> None:
    """Resolve the logged-in principal with its custom roles once, for the whole request."""
    principal = _cookie_principal(request)
    if principal is not None:
        principal = await role_service(request).attach(principal)
    request.state.admin_principal = principal


def session_principal(request: Request) -> Principal | None:
    """Return the logged-in principal of ``request``, honouring the absolute lifetime.

    Outside an admin router (where ``load_principal`` did not run) custom roles are left out,
    so the principal can only hold less.
    """
    loaded: Principal | None | bool = getattr(request.state, "admin_principal", False)
    if loaded is not False:
        return loaded if isinstance(loaded, Principal) else None
    return _cookie_principal(request)


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


def _return_path(request: Request) -> str:
    """Return the page to come back to after a fresh login.

    A GET returns to itself. A POST returns to the page that sent the form (the referer
    path); the login only accepts admin paths, so a forged referer cannot leave the portal.
    """
    if request.method == "GET":
        return request.url.path
    referer = request.headers.get("referer", "")
    path = referer.split("://", 1)[-1].partition("/")[2]
    return f"/{path.split('?', 1)[0]}" if path else "/admin/"


class RequirePermission:
    """FastAPI dependency: allow the request only if the matrix grants ``permission``.

    Args:
        permission: The one permission this route needs.
        group_param: Name of the path parameter with the group slug (group-level routes).
        organisation_param: Name of the path parameter with the organisation slug
            (organisation-level routes). Without either, a non-global permission must be
            held for at least one target.
        fresh: For changes and their forms: the login may be at most
            ``ADMIN_REAUTH_MINUTES`` old, so revoked roles stop working within that time.
    """

    def __init__(
        self,
        permission: Permission,
        group_param: str | None = None,
        *,
        organisation_param: str | None = None,
        fresh: bool = False,
    ) -> None:
        """Store the declaration; it is also read by the route-coverage test."""
        self.permission = permission
        self.group_param = group_param
        self.organisation_param = organisation_param
        self.fresh = fresh

    async def __call__(self, request: Request) -> AdminContext:
        """Resolve the principal and decide.

        Raises:
            LoginRequired: Without a valid session.
            AccessDenied: When the matrix refuses.
        """
        principal = session_principal(request)
        if principal is None:
            raise LoginRequired
        if self.fresh:
            self._require_recent_login(request)
        service = admin_service(request)
        if self.group_param is not None:
            slug = str(request.path_params[self.group_param])
            authorization = await service.authorize(principal, self.permission, group_slug=slug)
        elif self.organisation_param is not None:
            slug = str(request.path_params[self.organisation_param])
            authorization = await service.authorize(
                principal, self.permission, organisation_slug=slug
            )
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

    @staticmethod
    def _require_recent_login(request: Request) -> None:
        """Raise ``ReauthRequired`` when the login is older than the re-login limit."""
        clock: Clock = request.app.state.clock
        limit = timedelta(minutes=request.app.state.settings.admin_reauth_minutes)
        age = login_age(request, clock.now())
        if age is None or age > limit:
            raise ReauthRequired(_return_path(request))
