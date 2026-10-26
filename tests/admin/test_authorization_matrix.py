"""The authorisation matrix test (CLAUDE.md, ADR 0014).

Every role × every admin route × own and other organisation/group, against an expectation
table written by hand from HANDOVER §6. It must not be derived from the implementation.
"""

import pytest
from fastapi.routing import APIRoute

from app.admin.authz import RequirePermission
from app.admin.routes import PUBLIC_ADMIN_PATHS
from app.domain.authorization import Principal
from tests.admin.conftest import (
    GROUP_ADMIN,
    NO_ROLE,
    ORG_ADMIN,
    OUTSIDER,
    SAME_ORIGIN,
    SUPER,
    Site,
)
from tests.support.routes import all_routes

ROLES = {
    "superuser": SUPER,
    "org_admin (demo)": ORG_ADMIN,
    "group_admin (demo)": GROUP_ADMIN,
    "org_admin (andere)": OUTSIDER,
    "no role": NO_ROLE,
}

ROLE_ORDER = list(ROLES)


def statuses(*codes: int) -> dict[str, int]:
    """Expected status per role, in the order of ``ROLES``."""
    return dict(zip(ROLE_ORDER, codes, strict=True))


# Allowed POSTs answer 422 (empty form) or 404 (unknown item id); refused ones 403.
# "METHOD route template" -> concrete paths -> expected status per role (HANDOVER §6):
#   superuser · org_admin (demo) · group_admin (demo) · org_admin (andere) · no role
EXPECTED: dict[str, dict[str, dict[str, int]]] = {
    "GET /admin/": {"/admin/": statuses(200, 200, 200, 200, 403)},
    "GET /admin/rights": {"/admin/rights": statuses(200, 200, 200, 200, 403)},
    "GET /admin/matrix": {"/admin/matrix": statuses(200, 200, 200, 200, 403)},
    # ADR 0029: every role has a log; what it lists is exact per level (test_audit_routes).
    "GET /admin/audit": {"/admin/audit": statuses(200, 200, 200, 200, 403)},
    # Integrations and secrets: superuser only.
    "GET /admin/integrations": {"/admin/integrations": statuses(200, 403, 403, 403, 403)},
    # Organisations: create and edit, superuser only (HANDOVER §6).
    "GET /admin/organisations": {"/admin/organisations": statuses(200, 403, 403, 403, 403)},
    "GET /admin/organisations/new": {
        "/admin/organisations/new": statuses(200, 403, 403, 403, 403),
    },
    "POST /admin/organisations": {"/admin/organisations": statuses(422, 403, 403, 403, 403)},
    "GET /admin/organisations/{org}": {
        "/admin/organisations/demo": statuses(200, 403, 403, 403, 403),
        "/admin/organisations/andere": statuses(200, 403, 403, 403, 403),
        "/admin/organisations/nope": statuses(404, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}": {
        "/admin/organisations/demo": statuses(422, 403, 403, 403, 403),
        "/admin/organisations/nope": statuses(404, 403, 403, 403, 403),
    },
    # Branding per organisation: superuser only (HANDOVER §6), also for the own organisation.
    "GET /admin/branding": {"/admin/branding": statuses(200, 403, 403, 403, 403)},
    "GET /admin/organisations/{org}/branding": {
        "/admin/organisations/demo/branding": statuses(200, 403, 403, 403, 403),
        "/admin/organisations/andere/branding": statuses(200, 403, 403, 403, 403),
        "/admin/organisations/nope/branding": statuses(404, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/branding": {
        "/admin/organisations/demo/branding": statuses(422, 403, 403, 403, 403),
        "/admin/organisations/andere/branding": statuses(422, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/branding/logo": {
        "/admin/organisations/demo/branding/logo": statuses(422, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/branding/logo/remove": {
        "/admin/organisations/demo/branding/logo/remove": statuses(303, 403, 403, 403, 403),
    },
    # Holiday calendars (ADR 0025): the superuser maintains them; organisation admins choose
    # them for their own organisation; group admins choose for their own groups (below).
    "GET /admin/holidays": {"/admin/holidays": statuses(200, 200, 403, 200, 403)},
    "POST /admin/holidays": {"/admin/holidays": statuses(422, 403, 403, 403, 403)},
    "GET /admin/holidays/{calendar}": {
        "/admin/holidays/nationaal": statuses(200, 403, 403, 403, 403),
        "/admin/holidays/nope": statuses(404, 403, 403, 403, 403),
    },
    "POST /admin/holidays/{calendar}/days": {
        "/admin/holidays/nationaal/days": statuses(422, 403, 403, 403, 403),
    },
    "POST /admin/holidays/{calendar}/national": {
        "/admin/holidays/nationaal/national": statuses(422, 403, 403, 403, 403),
    },
    "POST /admin/holidays/{calendar}/days/{holiday_id}/delete": {
        "/admin/holidays/nationaal/days/999999/delete": statuses(404, 403, 403, 403, 403),
    },
    "POST /admin/holidays/{calendar}/delete": {
        "/admin/holidays/nope/delete": statuses(404, 403, 403, 403, 403),
    },
    "GET /admin/organisations/{org}/holidays": {
        "/admin/organisations/demo/holidays": statuses(200, 200, 403, 403, 403),
        "/admin/organisations/andere/holidays": statuses(200, 403, 403, 200, 403),
    },
    "POST /admin/organisations/{org}/holidays": {
        "/admin/organisations/demo/holidays": statuses(303, 303, 403, 403, 403),
        "/admin/organisations/andere/holidays": statuses(303, 403, 403, 303, 403),
    },
    "GET /admin/groups/{slug}/holidays": {
        "/admin/groups/demo/holidays": statuses(200, 200, 200, 403, 403),
        "/admin/groups/andere-team/holidays": statuses(200, 403, 403, 200, 403),
    },
    "POST /admin/groups/{slug}/holidays": {
        "/admin/groups/demo/holidays": statuses(422, 422, 422, 403, 403),
        "/admin/groups/andere-team/holidays": statuses(422, 403, 403, 422, 403),
    },
    "POST /admin/groups/{slug}/holidays/{day}/open": {
        "/admin/groups/demo/holidays/2026-12-25/open": statuses(404, 404, 404, 403, 403),
        "/admin/groups/andere-team/holidays/2026-12-25/open": statuses(404, 403, 403, 404, 403),
    },
    "POST /admin/groups/{slug}/holidays/{day}/close": {
        "/admin/groups/demo/holidays/2026-12-25/close": statuses(404, 404, 404, 403, 403),
        "/admin/groups/andere-team/holidays/2026-12-25/close": statuses(404, 403, 403, 404, 403),
    },
    # Machine API clients and switches (ADR 0023): superuser only. Unknown client ids: 404.
    "GET /admin/api": {"/admin/api": statuses(200, 403, 403, 403, 403)},
    "POST /admin/api/settings": {"/admin/api/settings": statuses(303, 403, 403, 403, 403)},
    "GET /admin/api/clients/new": {"/admin/api/clients/new": statuses(200, 403, 403, 403, 403)},
    "POST /admin/api/clients": {"/admin/api/clients": statuses(422, 403, 403, 403, 403)},
    "GET /admin/api/clients/{client_id}": {
        "/admin/api/clients/opc_unknown-client-0000": statuses(404, 403, 403, 403, 403),
    },
    "POST /admin/api/clients/{client_id}": {
        "/admin/api/clients/opc_unknown-client-0000": statuses(404, 403, 403, 403, 403),
    },
    "POST /admin/api/clients/{client_id}/rotate": {
        "/admin/api/clients/opc_unknown-client-0000/rotate": statuses(404, 403, 403, 403, 403),
    },
    "POST /admin/api/clients/{client_id}/enable": {
        "/admin/api/clients/opc_unknown-client-0000/enable": statuses(404, 403, 403, 403, 403),
    },
    "POST /admin/api/clients/{client_id}/revoke": {
        "/admin/api/clients/opc_unknown-client-0000/revoke": statuses(404, 403, 403, 403, 403),
    },
    # Message channels (SMTP, SMS gateway) per organisation: secrets, so superuser only.
    "GET /admin/organisations/{org}/channels": {
        "/admin/organisations/demo/channels": statuses(200, 403, 403, 403, 403),
        "/admin/organisations/andere/channels": statuses(200, 403, 403, 403, 403),
        "/admin/organisations/nope/channels": statuses(404, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/channels/email": {
        "/admin/organisations/demo/channels/email": statuses(422, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/channels/email/test": {
        "/admin/organisations/demo/channels/email/test": statuses(422, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/channels/sms": {
        "/admin/organisations/demo/channels/sms": statuses(422, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/channels/{channel}/remove": {
        "/admin/organisations/demo/channels/sms/remove": statuses(409, 403, 403, 403, 403),
        "/admin/organisations/demo/channels/fax/remove": statuses(404, 403, 403, 403, 403),
    },
    # Group creation: organisation admins (in their own organisation) and the superuser.
    "GET /admin/groups/new": {"/admin/groups/new": statuses(200, 200, 403, 200, 403)},
    "POST /admin/groups": {"/admin/groups": statuses(422, 422, 403, 422, 403)},
    "GET /admin/groups/{slug}": {
        "/admin/groups/demo": statuses(200, 200, 200, 403, 403),
        "/admin/groups/andere-team": statuses(200, 403, 403, 200, 403),
    },
    # Group settings (incl. routing to the WxCC queue): not for group admins.
    "GET /admin/groups/{slug}/settings": {
        "/admin/groups/demo/settings": statuses(200, 200, 403, 403, 403),
        "/admin/groups/andere-team/settings": statuses(200, 403, 403, 200, 403),
    },
    "POST /admin/groups/{slug}/settings": {
        "/admin/groups/demo/settings": statuses(422, 422, 403, 403, 403),
        "/admin/groups/andere-team/settings": statuses(422, 403, 403, 422, 403),
    },
    # Opening hours, exceptions and capacity: group admins too, for their own groups.
    "GET /admin/groups/{slug}/hours": {
        "/admin/groups/demo/hours": statuses(200, 200, 200, 403, 403),
        "/admin/groups/andere-team/hours": statuses(200, 403, 403, 200, 403),
    },
    "POST /admin/groups/{slug}/hours": {
        "/admin/groups/demo/hours": statuses(422, 422, 422, 403, 403),
        "/admin/groups/andere-team/hours": statuses(422, 403, 403, 422, 403),
    },
    "GET /admin/groups/{slug}/hours/{rule_id}": {
        "/admin/groups/demo/hours/999999": statuses(404, 404, 404, 403, 403),
        "/admin/groups/andere-team/hours/999999": statuses(404, 403, 403, 404, 403),
    },
    "POST /admin/groups/{slug}/hours/{rule_id}": {
        "/admin/groups/demo/hours/999999": statuses(422, 422, 422, 403, 403),
        "/admin/groups/andere-team/hours/999999": statuses(422, 403, 403, 422, 403),
    },
    "POST /admin/groups/{slug}/hours/{rule_id}/delete": {
        "/admin/groups/demo/hours/999999/delete": statuses(404, 404, 404, 403, 403),
        "/admin/groups/andere-team/hours/999999/delete": statuses(404, 403, 403, 404, 403),
    },
    "GET /admin/groups/{slug}/exceptions": {
        "/admin/groups/demo/exceptions": statuses(200, 200, 200, 403, 403),
        "/admin/groups/andere-team/exceptions": statuses(200, 403, 403, 200, 403),
    },
    "POST /admin/groups/{slug}/exceptions": {
        "/admin/groups/demo/exceptions": statuses(422, 422, 422, 403, 403),
        "/admin/groups/andere-team/exceptions": statuses(422, 403, 403, 422, 403),
    },
    # Reminder rules: organisation admins and the superuser, not group admins (HANDOVER §6).
    "GET /admin/groups/{slug}/reminders": {
        "/admin/groups/demo/reminders": statuses(200, 200, 403, 403, 403),
        "/admin/groups/andere-team/reminders": statuses(200, 403, 403, 200, 403),
    },
    "POST /admin/groups/{slug}/reminders": {
        "/admin/groups/demo/reminders": statuses(422, 422, 403, 403, 403),
        "/admin/groups/andere-team/reminders": statuses(422, 403, 403, 422, 403),
    },
    "POST /admin/groups/{slug}/reminders/{channel}/{minutes}/delete": {
        "/admin/groups/demo/reminders/sms/999/delete": statuses(404, 404, 403, 403, 403),
        "/admin/groups/andere-team/reminders/sms/999/delete": statuses(404, 403, 403, 404, 403),
    },
    # Message texts: group admins too, for their own groups ("mail texts", HANDOVER §6).
    "GET /admin/groups/{slug}/messages": {
        "/admin/groups/demo/messages": statuses(303, 303, 303, 403, 403),
        "/admin/groups/andere-team/messages": statuses(303, 403, 403, 303, 403),
    },
    "GET /admin/groups/{slug}/messages/{text_locale}": {
        "/admin/groups/demo/messages/nl": statuses(200, 200, 200, 403, 403),
        "/admin/groups/demo/messages/en": statuses(200, 200, 200, 403, 403),
        "/admin/groups/demo/messages/de": statuses(404, 404, 404, 403, 403),
        "/admin/groups/andere-team/messages/nl": statuses(200, 403, 403, 200, 403),
    },
    "POST /admin/groups/{slug}/messages/{text_locale}": {
        "/admin/groups/demo/messages/nl": statuses(303, 303, 303, 403, 403),
        "/admin/groups/andere-team/messages/nl": statuses(303, 403, 403, 303, 403),
    },
    # Agenda and timeline: bookings.view, also for group admins on their own groups.
    "GET /admin/groups/{slug}/agenda": {
        "/admin/groups/demo/agenda": statuses(200, 200, 200, 403, 403),
        "/admin/groups/andere-team/agenda": statuses(200, 403, 403, 200, 403),
    },
    "GET /admin/groups/{slug}/bookings/{booking_id}": {
        "/admin/groups/demo/bookings/999999": statuses(404, 404, 404, 403, 403),
        "/admin/groups/andere-team/bookings/999999": statuses(404, 403, 403, 404, 403),
    },
    "POST /admin/groups/{slug}/exceptions/{override_id}/delete": {
        "/admin/groups/demo/exceptions/999999/delete": statuses(404, 404, 404, 403, 403),
        "/admin/groups/andere-team/exceptions/999999/delete": statuses(404, 403, 403, 404, 403),
    },
    # Emergency closure of one group (ADR 0024): the team's own admins and organisation admins.
    "GET /admin/groups/{slug}/closure": {
        "/admin/groups/demo/closure": statuses(200, 200, 200, 403, 403),
        "/admin/groups/andere-team/closure": statuses(200, 403, 403, 200, 403),
    },
    "POST /admin/groups/{slug}/closure": {
        "/admin/groups/demo/closure": statuses(422, 422, 422, 403, 403),
        "/admin/groups/andere-team/closure": statuses(422, 403, 403, 422, 403),
    },
    # Without a closure there is nothing to update (422) or lift (404).
    "POST /admin/groups/{slug}/closure/update": {
        "/admin/groups/demo/closure/update": statuses(422, 422, 422, 403, 403),
    },
    "POST /admin/groups/{slug}/closure/lift": {
        "/admin/groups/demo/closure/lift": statuses(404, 404, 404, 403, 403),
        "/admin/groups/andere-team/closure/lift": statuses(404, 403, 403, 404, 403),
    },
    "POST /admin/groups/{slug}/closure/reinstate": {
        "/admin/groups/demo/closure/reinstate": statuses(303, 303, 303, 403, 403),
    },
    "POST /admin/groups/{slug}/closure/cancel": {
        "/admin/groups/demo/closure/cancel": statuses(303, 303, 303, 403, 403),
        "/admin/groups/andere-team/closure/cancel": statuses(303, 403, 403, 303, 403),
    },
    "POST /admin/groups/{slug}/closure/bookings/{booking_id}/reinstate": {
        "/admin/groups/demo/closure/bookings/999999/reinstate": statuses(404, 404, 404, 403, 403),
    },
    "POST /admin/groups/{slug}/closure/bookings/{booking_id}/cancel": {
        "/admin/groups/demo/closure/bookings/999999/cancel": statuses(404, 404, 404, 403, 403),
        "/admin/groups/andere-team/closure/bookings/999999/cancel": statuses(
            404, 403, 403, 404, 403
        ),
    },
    "GET /admin/groups/{slug}/closure/bookings/{booking_id}/move": {
        "/admin/groups/demo/closure/bookings/999999/move": statuses(404, 404, 404, 403, 403),
    },
    # An empty move form has no slot: 400 before anything is looked up.
    "POST /admin/groups/{slug}/closure/bookings/{booking_id}/move": {
        "/admin/groups/demo/closure/bookings/999999/move": statuses(400, 400, 400, 403, 403),
    },
    # Emergency closure of a whole organisation: organisation admins and the superuser.
    "GET /admin/closures": {"/admin/closures": statuses(200, 200, 403, 200, 403)},
    "GET /admin/organisations/{org}/closure": {
        "/admin/organisations/demo/closure": statuses(200, 200, 403, 403, 403),
        "/admin/organisations/andere/closure": statuses(200, 403, 403, 200, 403),
        "/admin/organisations/nope/closure": statuses(404, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/closure": {
        "/admin/organisations/demo/closure": statuses(422, 422, 403, 403, 403),
        "/admin/organisations/andere/closure": statuses(422, 403, 403, 422, 403),
    },
    "POST /admin/organisations/{org}/closure/lift": {
        "/admin/organisations/demo/closure/lift": statuses(303, 303, 403, 403, 403),
    },
}

CASES = [
    (role, key.split(" ", 1)[0], path, status)
    for key, routes in EXPECTED.items()
    for path, by_role in routes.items()
    for role, status in by_role.items()
]


@pytest.mark.parametrize(("role", "method", "path", "status"), CASES)
async def test_matrix(site: Site, role: str, method: str, path: str, status: int) -> None:
    await site.login_as(ROLES[role])

    if method == "GET":
        response = await site.client.get(path)
    else:
        response = await site.client.post(path, headers=SAME_ORIGIN)

    assert response.status_code == status, (role, method, path)


GET_PATHS = [p for key, routes in EXPECTED.items() if key.startswith("GET ") for p in routes]


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
