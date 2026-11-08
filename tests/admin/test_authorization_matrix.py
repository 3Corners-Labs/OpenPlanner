"""The authorisation matrix test (CLAUDE.md, ADR 0014).

Every role × every admin route × own and other organisation/group, against an expectation
table written by hand from HANDOVER §6 (``EXPECTED`` in ``tests/support/authorization_matrix.py``).
It must not be derived from the implementation.

The sweeps over role × route carry the ``matrix`` marker: they run in the merge queue and on a
pull request labelled ``ci:full``, not on every push (ADR 0058). The structural tests at the
end of this file run everywhere, so a route without a permission is still caught at once.
"""

import pytest
from fastapi.routing import APIRoute

from app.admin.authz import RequirePermission
from app.admin.routes import PUBLIC_ADMIN_PATHS
from app.domain.authorization import Principal
from app.domain.maintenance import MaintenanceReason
from app.domain.maintenance_service import MaintenanceService
from tests.admin.conftest import SAME_ORIGIN, Site
from tests.support.authorization_matrix import EXPECTED, ROLES
from tests.support.routes import all_routes, scoped_routes

CASES = [
    (role, key.split(" ", 1)[0], path, status)
    for key, routes in EXPECTED.items()
    for path, by_role in routes.items()
    for role, status in by_role.items()
]


@pytest.mark.matrix
@pytest.mark.parametrize(("role", "method", "path", "status"), CASES)
async def test_matrix(site: Site, role: str, method: str, path: str, status: int) -> None:
    assert await answer(site, role, method, path) == status, (role, method, path)


async def answer(site: Site, role: str, method: str, path: str) -> int:
    """Log in as ``role`` and return the status of an empty request to ``path``."""
    await site.login_as(ROLES[role])
    if method == "GET":
        return (await site.client.get(path)).status_code
    return (await site.client.post(path, headers=SAME_ORIGIN)).status_code


# Maintenance (ADR 0038 §2-3), written out by hand from the ADR's table: while it is on, only
# these changes go ahead, and only for the superuser. Integrations (SMTP, SMS, the test mail),
# updates, the switch itself and "Koppeling controleren" (maintenance_safe).
WRITABLE_IN_MAINTENANCE = frozenset(
    {
        "POST /admin/organisations/{org}/channels/email",
        "POST /admin/organisations/{org}/channels/email/test",
        "POST /admin/organisations/{org}/channels/email/domain",
        "POST /admin/organisations/{org}/channels/sms",
        "POST /admin/organisations/{org}/channels/{channel}/remove",
        "POST /admin/updates/check",
        "POST /admin/updates/update",
        "POST /admin/updates/restore",
        "POST /admin/updates/requests/{request_id}/cancel",
        "POST /admin/updates/snooze",
        "POST /admin/maintenance/on",
        "POST /admin/maintenance/off",
        "POST /admin/groups/{slug}/check",
        "POST /admin/integrations/service-app-check",
        # ADR 0082 point 9: an organisation's alert targets fall under integrations.manage, which
        # stays writable for the superuser during maintenance (a repaired channel is tested).
        "POST /admin/organisations/{org}/channels/alerts/targets",
        "POST /admin/organisations/{org}/channels/alerts/targets/{target_id}/remove",
        "POST /admin/organisations/{org}/channels/alerts/targets/{target_id}/test",
        "POST /admin/organisations/{org}/channels/alerts/bot",
    }
)


def in_maintenance(key: str, role: str, status: int) -> int:
    """The expected status while maintenance is on: the matrix refuses first (403); of what
    it allows, only the writable changes of the superuser go ahead; the rest is 503."""
    if status == 403 or key.startswith("GET "):
        return status
    if key in WRITABLE_IN_MAINTENANCE and role == "superuser":
        return status
    return 503


def maintenance_relevant(key: str, role: str, status: int) -> bool:
    """Whether a case says something maintenance changes (ADR 0038: every endpoint once).

    The superuser runs every endpoint, which covers each one once and every writable change.
    Of the other roles, a POST the matrix allows must now get 503, and "no role" on every POST
    proves the matrix refuses (403) before maintenance does. A GET or a refused POST of
    another role answers exactly as in ``test_matrix``, which runs those cases already.
    """
    if role == "superuser":
        return True
    if not key.startswith("POST "):
        return False
    return status != 403 or role == "no role"


MAINTENANCE_CASES = [
    (role, key.split(" ", 1)[0], path, in_maintenance(key, role, status))
    for key, routes in EXPECTED.items()
    for path, by_role in routes.items()
    for role, status in by_role.items()
    if maintenance_relevant(key, role, status)
]


@pytest.mark.matrix
@pytest.mark.parametrize(("role", "method", "path", "status"), MAINTENANCE_CASES)
async def test_matrix_during_maintenance(
    site: Site, role: str, method: str, path: str, status: int
) -> None:
    await maintenance_on(site)

    assert await answer(site, role, method, path) == status, (role, method, path)


def test_writable_routes_exist() -> None:
    assert WRITABLE_IN_MAINTENANCE <= set(EXPECTED)


def test_the_maintenance_sweep_covers_every_endpoint_and_every_allowed_change() -> None:
    swept = {(role, path) for role, _, path, _ in MAINTENANCE_CASES}
    every_path = {path for routes in EXPECTED.values() for path in routes}
    allowed_posts = {
        (role, path)
        for key, routes in EXPECTED.items()
        if key.startswith("POST ")
        for path, by_role in routes.items()
        for role, status in by_role.items()
        if status != 403
    }

    assert {path for role, path in swept if role == "superuser"} == every_path
    assert allowed_posts <= swept


async def maintenance_on(site: Site) -> None:
    """Switch maintenance on as the host would (``python -m app.cli maintenance on``)."""
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    service: MaintenanceService = app.state.maintenance_service
    await service.switch_on_host("host-cli", MaintenanceReason.OTHER)


GET_PATHS = [p for key, routes in EXPECTED.items() if key.startswith("GET ") for p in routes]


@pytest.mark.matrix
@pytest.mark.parametrize("path", GET_PATHS)
async def test_anonymous_visitors_are_sent_to_the_login(site: Site, path: str) -> None:
    response = await site.client.get(path)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login?next=/admin")


def _requirements(route: APIRoute) -> list[RequirePermission]:
    found: list[RequirePermission] = []
    stack = list(route.dependant.dependencies)
    while stack:
        dependency = stack.pop()
        if isinstance(dependency.call, RequirePermission):
            found.append(dependency.call)
        stack.extend(dependency.dependencies)
    return found


def test_every_admin_route_declares_one_permission_and_is_in_the_table(site: Site) -> None:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    routes = [r for r in all_routes(app.routes) if r.path.startswith("/admin")]
    protected = {
        f"{method} {r.path}": r
        for r in routes
        if r.path not in PUBLIC_ADMIN_PATHS
        for method in r.methods
    }

    assert set(protected) == set(EXPECTED), "admin route missing from the matrix test"
    for path, route in protected.items():
        assert len(_requirements(route)) == 1, f"{path} must declare exactly one permission"
    for path in PUBLIC_ADMIN_PATHS:
        assert not any(_requirements(r) for r in routes if r.path == path), path


def test_expectations_cover_every_role(site: Site) -> None:
    for routes in EXPECTED.values():
        for by_role in routes.values():
            assert set(by_role) == set(ROLES)
    assert all(isinstance(p, Principal) for p in ROLES.values())


# WP-B2 (TEN-01): a route that addresses an organisation or a group is checked before release,
# automatically. Admin routes sit in EXPECTED with a case that refuses an administrator of
# the other organisation. The routes below it are open to participants by design: they answer
# for the slug in the path and are proved tenant by tenant in test_tenant_isolation_concurrent.
PUBLIC_SCOPED = frozenset(
    {
        "GET /api/v1/widget/{org}/{group}/",
        "GET /api/v1/widget/{org}/{group}/availability",
        "OPTIONS /api/v1/widget/{org}/{group}/bookings",
        "POST /api/v1/widget/{org}/{group}/bookings",
        "GET /g/{slug}",
        "GET /g/{slug}/book",
        "GET /g/{slug}/booked",
        "GET /g/{slug}/closure",
        "GET /g/{slug}/closure/image",
        "GET /g/{slug}/direct",
        "POST /g/{slug}/book",
        "POST /g/{slug}/direct",
        "GET /o/{slug}/font/{role}",
        "GET /o/{slug}/logo",
        "GET /o/{slug}/theme.css",
    }
)
QUERY_SCOPED = frozenset({"GET /admin/audit", "GET /admin/audit/export.csv", "GET /admin/staff"})
"""Admin lists whose scope comes from the principal; ``group`` or ``organisation`` in the query
only narrows. ``test_privacy_sweep_exports`` proves a narrowing cannot widen."""


def refuses_the_other_organisation(by_path: dict[str, dict[str, int]]) -> bool:
    """Whether one case shows an administrator of the other organisation refused.

    A path under ``andere`` is tried by the organisation admin of ``demo``, any other path by
    the organisation admin of ``andere``; both must get 403 or 404.
    """
    for path, by_role in by_path.items():
        if "andere" in path:
            other = by_role["org_admin (demo)"]
        elif "demo" in path:
            other = by_role["org_admin (andere)"]
        else:
            continue
        if other in {403, 404}:
            return True
    return False


def test_every_route_with_an_organisation_or_group_is_in_the_matrix_sweep(site: Site) -> None:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    scoped = scoped_routes(all_routes(app.routes))

    admin = {k for k in scoped if k.split(" ", 1)[1].startswith("/admin")}
    missing = sorted(admin - set(EXPECTED))
    assert missing == [], f"add these to the matrix sweep (EXPECTED): {missing}"
    unproved = sorted(
        k for k in admin - QUERY_SCOPED if not refuses_the_other_organisation(EXPECTED[k])
    )
    assert unproved == [], f"no case refuses the other organisation for: {unproved}"
    assert QUERY_SCOPED <= admin
    outside = set(scoped) - admin
    assert outside == PUBLIC_SCOPED, (
        f"a route addressing an organisation or group: {sorted(outside ^ PUBLIC_SCOPED)}; "
        "decide who may call it and add it to the sweep or to PUBLIC_SCOPED"
    )
