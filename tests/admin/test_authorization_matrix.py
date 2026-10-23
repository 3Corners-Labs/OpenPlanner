"""The authorisation matrix test (CLAUDE.md, ADR 0014).

Every role × every admin route × own and other organisation/group, against an expectation
table written by hand from HANDOVER §6. It must not be derived from the implementation.
"""

import pytest
from fastapi.routing import APIRoute

from app.admin.authz import RequirePermission
from app.admin.routes import PUBLIC_ADMIN_PATHS
from app.domain.authorization import Principal
from tests.admin.conftest import GROUP_ADMIN, NO_ROLE, ORG_ADMIN, OUTSIDER, SUPER, Site

ROLES = {
    "superuser": SUPER,
    "org_admin (demo)": ORG_ADMIN,
    "group_admin (demo)": GROUP_ADMIN,
    "org_admin (andere)": OUTSIDER,
    "no role": NO_ROLE,
}

# route template -> concrete paths -> expected status per role (HANDOVER §6)
EXPECTED: dict[str, dict[str, dict[str, int]]] = {
    "/admin/": {
        "/admin/": {
            "superuser": 200,
            "org_admin (demo)": 200,
            "group_admin (demo)": 200,
            "org_admin (andere)": 200,
            "no role": 403,
        }
    },
    "/admin/rights": {
        "/admin/rights": {
            "superuser": 200,
            "org_admin (demo)": 200,
            "group_admin (demo)": 200,
            "org_admin (andere)": 200,
            "no role": 403,
        }
    },
    "/admin/matrix": {
        "/admin/matrix": {
            "superuser": 200,
            "org_admin (demo)": 200,
            "group_admin (demo)": 200,
            "org_admin (andere)": 200,
            "no role": 403,
        }
    },
    "/admin/audit": {
        "/admin/audit": {
            "superuser": 200,
            "org_admin (demo)": 200,
            "group_admin (demo)": 403,
            "org_admin (andere)": 200,
            "no role": 403,
        }
    },
    "/admin/groups/{slug}": {
        "/admin/groups/demo": {
            "superuser": 200,
            "org_admin (demo)": 200,
            "group_admin (demo)": 200,
            "org_admin (andere)": 403,
            "no role": 403,
        },
        "/admin/groups/andere-team": {
            "superuser": 200,
            "org_admin (demo)": 403,
            "group_admin (demo)": 403,
            "org_admin (andere)": 200,
            "no role": 403,
        },
    },
}

CASES = [
    (role, path, status)
    for routes in EXPECTED.values()
    for path, by_role in routes.items()
    for role, status in by_role.items()
]


@pytest.mark.parametrize(("role", "path", "status"), CASES)
async def test_matrix(site: Site, role: str, path: str, status: int) -> None:
    await site.login_as(ROLES[role])

    response = await site.client.get(path)

    assert response.status_code == status, (role, path)


@pytest.mark.parametrize("path", [p for routes in EXPECTED.values() for p in routes])
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


def _all_routes(routes: list[object]) -> list[APIRoute]:
    """Flatten the app's routes, including routers mounted with include_router."""
    found: list[APIRoute] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
        inner = getattr(route, "original_router", None)  # FastAPI's included-router wrapper
        if inner is not None:
            found.extend(_all_routes(inner.routes))
    return found


def test_every_admin_route_declares_one_permission_and_is_in_the_table(site: Site) -> None:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    routes = [r for r in _all_routes(app.routes) if r.path.startswith("/admin")]
    protected = {r.path: r for r in routes if r.path not in PUBLIC_ADMIN_PATHS}

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
