"""The authorisation matrix test (CLAUDE.md, ADR 0014).

Every role × every admin route × own and other organisation/group, against an expectation
table written by hand from HANDOVER §6. It must not be derived from the implementation.

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
from tests.admin.conftest import (
    AGENT,
    AUDITOR,
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
    "auditor (demo)": AUDITOR,
    "agent (demo)": AGENT,
    "no role": NO_ROLE,
}

ROLE_ORDER = list(ROLES)


def statuses(*codes: int) -> dict[str, int]:
    """Expected status per role, in the order of ``ROLES``."""
    return dict(zip(ROLE_ORDER, codes, strict=True))


# Allowed POSTs answer 422 (empty form) or 404 (unknown item id); refused ones 403.
# "METHOD route template" -> concrete paths -> expected status per role (HANDOVER §6):
#   superuser · org_admin (demo) · group_admin (demo) · org_admin (andere) · auditor (demo) ·
#   agent (demo) · no role. The auditor (ADR 0039) reads: overview, rights, matrix, log,
#   retention, staff, manual, group page, and the settings (ADR 0042). The agent (ADR 0043)
#   reads overview, rights, matrix, manual and group page, and works the own queue of its groups.
EXPECTED: dict[str, dict[str, dict[str, int]]] = {
    "GET /admin/": {"/admin/": statuses(200, 200, 200, 200, 200, 200, 403)},
    "GET /admin/rights": {"/admin/rights": statuses(200, 200, 200, 200, 200, 200, 403)},
    # ADR 0061: overview widgets, each with the permission of the widget register. The
    # auditor and the agent hold no bookings.view; the agent holds no audit.view.
    "GET /admin/overview/w/rights": {
        "/admin/overview/w/rights": statuses(200, 200, 200, 200, 200, 200, 403)
    },
    "GET /admin/overview/w/bookings-today": {
        "/admin/overview/w/bookings-today": statuses(200, 200, 200, 200, 403, 403, 403)
    },
    "GET /admin/overview/w/changes": {
        "/admin/overview/w/changes": statuses(200, 200, 200, 200, 200, 403, 403)
    },
    # queue.work: not the auditor (no personal data); tasks: dashboard.view, every admin;
    # status: settings.platform.view, the superuser and the auditor only.
    "GET /admin/overview/w/queue": {
        "/admin/overview/w/queue": statuses(200, 200, 200, 200, 403, 200, 403)
    },
    "GET /admin/overview/w/tasks": {
        "/admin/overview/w/tasks": statuses(200, 200, 200, 200, 200, 200, 403)
    },
    "GET /admin/overview/w/status": {
        "/admin/overview/w/status": statuses(200, 403, 403, 403, 200, 403, 403)
    },
    "GET /admin/matrix": {"/admin/matrix": statuses(200, 200, 200, 200, 200, 200, 403)},
    # ADR 0039: the role editor is the superuser's (roles.manage is locked).
    "GET /admin/roles": {"/admin/roles": statuses(200, 403, 403, 403, 403, 403, 403)},
    "GET /admin/roles/new": {"/admin/roles/new": statuses(200, 403, 403, 403, 403, 403, 403)},
    "POST /admin/roles": {"/admin/roles": statuses(422, 403, 403, 403, 403, 403, 403)},
    "GET /admin/roles/{role_id}": {
        "/admin/roles/999999": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/roles/{role_id}": {
        "/admin/roles/999999": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/roles/{role_id}/delete": {
        "/admin/roles/999999/delete": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    # ADR 0031: every role has a log; what it lists is exact per level (test_audit_routes).
    "GET /admin/audit": {"/admin/audit": statuses(200, 200, 200, 200, 200, 403, 403)},
    # ADR 0040: the audit log as CSV, for compliance roles; not for group admins.
    "GET /admin/audit/export.csv": {
        "/admin/audit/export.csv": statuses(200, 200, 403, 200, 200, 403, 403)
    },
    # ADR 0040, part 4: diagnostic mode, superuser only (product owner, 2026-10-29). An empty
    # form names no target and changes nothing.
    "POST /admin/audit/diagnostics": {
        "/admin/audit/diagnostics": statuses(303, 403, 403, 403, 403, 403, 403)
    },
    "POST /admin/audit/diagnostics/off": {
        "/admin/audit/diagnostics/off": statuses(303, 403, 403, 403, 403, 403, 403)
    },
    # ADR 0039: retention counts per organisation; no group admins, never personal data.
    "GET /admin/retention": {"/admin/retention": statuses(200, 200, 403, 200, 200, 403, 403)},
    # ADR 0042 §5, §11: System status and Advanced settings, read-only: superuser and auditor.
    "GET /admin/settings": {"/admin/settings": statuses(200, 403, 403, 403, 200, 403, 403)},
    # ADR 0101: the Settings sub-tab is read like the status; the package is the superuser's.
    "GET /admin/settings/values": {
        "/admin/settings/values": statuses(200, 403, 403, 403, 200, 403, 403)
    },
    "GET /admin/settings/package": {
        "/admin/settings/package": statuses(200, 403, 403, 403, 403, 403, 403)
    },
    # ADR 0042 step 4: changing is the superuser's (settings.platform.manage is locked). An
    # empty value is outside the bounds (422); resetting what is not set returns to the page.
    "POST /admin/settings/{key}": {
        "/admin/settings/admin_session_hours": statuses(422, 403, 403, 403, 403, 403, 403),
    },
    # One Save per topic: an empty form changes nothing and returns to the page.
    "POST /admin/settings/topic/{topic}": {
        "/admin/settings/topic/admin_session": statuses(303, 403, 403, 403, 403, 403, 403),
    },
    # ADR 0042 step 5: the own organisation's class D settings; org admins and the superuser.
    # ADR 0047 §4: the old list redirects permanently to the organisation's Settings sub-tab.
    "GET /admin/organisation-settings": {
        "/admin/organisation-settings": statuses(308, 308, 403, 308, 403, 403, 403),
    },
    "GET /admin/organisations/{org}/settings": {
        "/admin/organisations/demo/settings": statuses(200, 200, 403, 403, 403, 403, 403),
        "/admin/organisations/andere/settings": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/settings": {
        "/admin/organisations/demo/settings": statuses(303, 303, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/settings/{key}": {
        "/admin/organisations/demo/settings/time_format": statuses(
            422, 422, 403, 403, 403, 403, 403
        ),
    },
    "POST /admin/organisations/{org}/settings/{key}/reset": {
        "/admin/organisations/demo/settings/time_format/reset": statuses(
            303, 303, 403, 403, 403, 403, 403
        ),
    },
    # ADR 0074: the organisation's own texts; org admins (own organisation) and the superuser.
    "GET /admin/organisations/{org}/texts": {
        "/admin/organisations/demo/texts": statuses(303, 303, 403, 403, 403, 403, 403),
        "/admin/organisations/andere/texts": statuses(303, 403, 403, 303, 403, 403, 403),
    },
    "GET /admin/organisations/{org}/texts/{text_locale}": {
        "/admin/organisations/demo/texts/nl": statuses(200, 200, 403, 403, 403, 403, 403),
        "/admin/organisations/andere/texts/nl": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/texts/{text_locale}": {
        "/admin/organisations/demo/texts/nl": statuses(303, 303, 403, 403, 403, 403, 403),
    },
    # ADR 0042 §12: taking a value over from the environment; nothing is locked here (409).
    "POST /admin/settings/{key}/adopt": {
        "/admin/settings/admin_session_hours/adopt": statuses(409, 403, 403, 403, 403, 403, 403),
    },
    # ADR 0042 §8: the login mapping, superuser only (settings.login.manage); an empty form is
    # refused before any login.
    "POST /admin/settings/login/test": {
        "/admin/settings/login/test": statuses(422, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/settings/{key}/reset": {
        "/admin/settings/admin_session_hours/reset": statuses(303, 403, 403, 403, 403, 403, 403),
    },
    # ADR 0072: the certificate source, the key on this host and the chain, superuser only.
    # Without the bundled proxy (the test settings) a request or an upload is refused (422), a
    # removal is a conflict (409), there is no request to download (404); *Check now* always
    # probes and returns to the page.
    "POST /admin/settings/tls/csr": {
        "/admin/settings/tls/csr": statuses(422, 403, 403, 403, 403, 403, 403),
    },
    "GET /admin/settings/tls/csr.pem": {
        "/admin/settings/tls/csr.pem": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/settings/tls/certificate": {
        "/admin/settings/tls/certificate": statuses(422, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/settings/tls/certificate/remove": {
        "/admin/settings/tls/certificate/remove": statuses(409, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/settings/tls/check": {
        "/admin/settings/tls/check": statuses(303, 403, 403, 403, 403, 403, 403),
    },
    # ADR 0089: the configuration package; an empty form is a short passphrase or no file.
    "POST /admin/settings/package/export": {
        "/admin/settings/package/export": statuses(422, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/settings/package/import": {
        "/admin/settings/package/import": statuses(422, 403, 403, 403, 403, 403, 403),
    },
    # ADR 0046: staff in the own scope; an unknown or invisible person is a 404, not a 403.
    "GET /admin/staff": {"/admin/staff": statuses(200, 200, 200, 200, 200, 403, 403)},
    # ADR 0049 point 17: the mapping page is an input for the access review (organisation
    # level): the superuser, org admins and the auditor; never a group admin or an agent.
    "GET /admin/idp-mapping": {"/admin/idp-mapping": statuses(200, 200, 403, 200, 200, 403, 403)},
    "GET /admin/idp-mapping/export.txt": {
        "/admin/idp-mapping/export.txt": statuses(200, 200, 403, 200, 200, 403, 403)
    },
    # ADR 0049 point 4: a group's IdP value, by the org admin of its organisation (an empty
    # value is outside the grammar, 422); a group admin may not change their own group's.
    "POST /admin/groups/{slug}/settings/idp-key": {
        "/admin/groups/demo/settings/idp-key": statuses(422, 422, 403, 403, 403, 403, 403),
    },
    # ADR 0095: without a bundled provider the form sends the admin back to the list (303), a
    # change finds no directory (an error page, 200) and an empty invitation is refused (422);
    # the permission is the organisation admin's and the superuser's, no one else's.
    "GET /admin/staff/invite": {"/admin/staff/invite": statuses(303, 303, 403, 303, 403, 403, 403)},
    "POST /admin/staff/invite": {
        "/admin/staff/invite": statuses(422, 422, 403, 422, 403, 403, 403)
    },
    "POST /admin/staff/directory/grants/add": {
        "/admin/staff/directory/grants/add": statuses(303, 303, 403, 303, 403, 403, 403)
    },
    "POST /admin/staff/directory/grants/remove": {
        "/admin/staff/directory/grants/remove": statuses(303, 303, 403, 303, 403, 403, 403)
    },
    "POST /admin/staff/directory/enabled": {
        "/admin/staff/directory/enabled": statuses(200, 200, 403, 200, 403, 403, 403)
    },
    "POST /admin/staff/directory/reset": {
        "/admin/staff/directory/reset": statuses(200, 200, 403, 200, 403, 403, 403)
    },
    "GET /admin/staff/{member_id}": {
        "/admin/staff/999": statuses(404, 404, 404, 404, 404, 403, 403)
    },
    # ADR 0033: the manual is public, every admin may read every topic.
    # Step 2b: the profile menu's theme and alert choices, stored in the own session (303 back).
    "GET /admin/preferences": {"/admin/preferences": statuses(303, 303, 303, 303, 303, 303, 403)},
    "GET /admin/help": {"/admin/help": statuses(200, 200, 200, 200, 200, 200, 403)},
    "GET /admin/help/{topic}": {
        "/admin/help/group-settings": statuses(200, 200, 200, 200, 200, 200, 403),
        "/admin/help/nope": statuses(404, 404, 404, 404, 404, 404, 403),
    },
    # Integrations and secrets: superuser only.
    "GET /admin/integrations": {"/admin/integrations": statuses(200, 403, 403, 403, 403, 403, 403)},
    # ADR 0082: the Service App check from Koppelingen; whoever manages integrations.
    "POST /admin/integrations/service-app-check": {
        "/admin/integrations/service-app-check": statuses(303, 403, 403, 403, 403, 403, 403)
    },
    # Organisations: create and edit, superuser only (HANDOVER §6).
    # ADR 0047 step 3: organisation.view opens the organisations in reach; an admin of exactly
    # one organisation is sent straight to it (303). The auditor reads it too.
    "GET /admin/organisations": {
        "/admin/organisations": statuses(200, 303, 403, 303, 303, 403, 403)
    },
    "GET /admin/groups": {"/admin/groups": statuses(200, 200, 200, 200, 200, 200, 403)},
    "GET /admin/organisations/new": {
        "/admin/organisations/new": statuses(200, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations": {
        "/admin/organisations": statuses(422, 403, 403, 403, 403, 403, 403)
    },
    "GET /admin/organisations/{org}": {
        "/admin/organisations/demo": statuses(200, 200, 403, 403, 200, 403, 403),
        "/admin/organisations/andere": statuses(200, 403, 403, 200, 403, 403, 403),
        "/admin/organisations/nope": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    # Editing name and language stays with the superuser (organisation.manage is locked).
    "GET /admin/organisations/{org}/edit": {
        "/admin/organisations/demo/edit": statuses(200, 403, 403, 403, 403, 403, 403),
        "/admin/organisations/andere/edit": statuses(200, 403, 403, 403, 403, 403, 403),
        "/admin/organisations/nope/edit": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}": {
        "/admin/organisations/demo": statuses(422, 403, 403, 403, 403, 403, 403),
        "/admin/organisations/nope": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    # ADR 0084: deleting is the superuser's own right (organisation.delete, locked); an empty
    # confirmation is refused (422).
    "GET /admin/organisations/{org}/delete": {
        "/admin/organisations/demo/delete": statuses(200, 403, 403, 403, 403, 403, 403),
        "/admin/organisations/nope/delete": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/delete": {
        "/admin/organisations/demo/delete": statuses(422, 403, 403, 403, 403, 403, 403),
        "/admin/organisations/nope/delete": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    # Branding per organisation: superuser only (HANDOVER §6), also for the own organisation.
    # ADR 0047 §4: the old branding list redirects permanently to the organisations.
    "GET /admin/branding": {"/admin/branding": statuses(308, 403, 403, 403, 403, 403, 403)},
    "GET /admin/organisations/{org}/branding": {
        "/admin/organisations/demo/branding": statuses(200, 403, 403, 403, 403, 403, 403),
        "/admin/organisations/andere/branding": statuses(200, 403, 403, 403, 403, 403, 403),
        "/admin/organisations/nope/branding": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/branding": {
        "/admin/organisations/demo/branding": statuses(422, 403, 403, 403, 403, 403, 403),
        "/admin/organisations/andere/branding": statuses(422, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/branding/logo": {
        "/admin/organisations/demo/branding/logo": statuses(422, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/branding/logo/remove": {
        "/admin/organisations/demo/branding/logo/remove": statuses(
            303, 403, 403, 403, 403, 403, 403
        ),
    },
    # ADR 0041: theme file, fonts. Same rule: superuser only.
    "GET /admin/organisations/{org}/branding/theme.css": {
        "/admin/organisations/demo/branding/theme.css": statuses(200, 403, 403, 403, 403, 403, 403),
        "/admin/organisations/andere/branding/theme.css": statuses(
            200, 403, 403, 403, 403, 403, 403
        ),
    },
    "POST /admin/organisations/{org}/branding/theme": {
        "/admin/organisations/demo/branding/theme": statuses(422, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/branding/font/{role}": {
        "/admin/organisations/demo/branding/font/text": statuses(422, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/branding/font/{role}/remove": {
        "/admin/organisations/demo/branding/font/heading/remove": statuses(
            303, 403, 403, 403, 403, 403, 403
        ),
    },
    # Holiday calendars (ADR 0025): the superuser maintains them; organisation admins choose
    # them for their own organisation; group admins choose for their own groups (below).
    # ADR 0047 §4: the shared calendars are the superuser's (Platform → Holiday calendars);
    # an organisation chooses its calendars on its own Holidays tab.
    "GET /admin/holidays": {"/admin/holidays": statuses(200, 403, 403, 403, 403, 403, 403)},
    "POST /admin/holidays": {"/admin/holidays": statuses(422, 403, 403, 403, 403, 403, 403)},
    "GET /admin/holidays/{calendar}": {
        "/admin/holidays/nationaal": statuses(200, 403, 403, 403, 403, 403, 403),
        "/admin/holidays/nope": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    # ADR 0084 step 5: renaming is holidays.manage like the rest; an empty name is refused (422).
    "POST /admin/holidays/{calendar}": {
        "/admin/holidays/nationaal": statuses(422, 403, 403, 403, 403, 403, 403),
        "/admin/holidays/nope": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/holidays/{calendar}/days": {
        "/admin/holidays/nationaal/days": statuses(422, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/holidays/{calendar}/national": {
        "/admin/holidays/nationaal/national": statuses(422, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/holidays/{calendar}/days/{holiday_id}/delete": {
        "/admin/holidays/nationaal/days/999999/delete": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/holidays/{calendar}/delete": {
        "/admin/holidays/nope/delete": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "GET /admin/organisations/{org}/holidays": {
        "/admin/organisations/demo/holidays": statuses(200, 200, 403, 403, 403, 403, 403),
        "/admin/organisations/andere/holidays": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/holidays": {
        "/admin/organisations/demo/holidays": statuses(303, 303, 403, 403, 403, 403, 403),
        "/admin/organisations/andere/holidays": statuses(303, 403, 403, 303, 403, 403, 403),
    },
    "GET /admin/groups/{slug}/holidays": {
        "/admin/groups/demo/holidays": statuses(200, 200, 200, 403, 403, 403, 403),
        "/admin/groups/andere-team/holidays": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/holidays": {
        "/admin/groups/demo/holidays": statuses(422, 422, 422, 403, 403, 403, 403),
        "/admin/groups/andere-team/holidays": statuses(422, 403, 403, 422, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/holidays/{day}/open": {
        "/admin/groups/demo/holidays/2026-12-25/open": statuses(404, 404, 404, 403, 403, 403, 403),
        "/admin/groups/andere-team/holidays/2026-12-25/open": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "POST /admin/groups/{slug}/holidays/{day}/close": {
        "/admin/groups/demo/holidays/2026-12-25/close": statuses(404, 404, 404, 403, 403, 403, 403),
        "/admin/groups/andere-team/holidays/2026-12-25/close": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    # Machine API clients and switches (ADR 0023): superuser only. Unknown client ids: 404.
    "GET /admin/api": {"/admin/api": statuses(200, 403, 403, 403, 403, 403, 403)},
    "POST /admin/api/settings": {
        "/admin/api/settings": statuses(303, 403, 403, 403, 403, 403, 403)
    },
    "GET /admin/api/clients/new": {
        "/admin/api/clients/new": statuses(200, 403, 403, 403, 403, 403, 403)
    },
    "POST /admin/api/clients": {"/admin/api/clients": statuses(422, 403, 403, 403, 403, 403, 403)},
    "GET /admin/api/clients/{client_id}": {
        "/admin/api/clients/opc_unknown-client-0000": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/api/clients/{client_id}": {
        "/admin/api/clients/opc_unknown-client-0000": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/api/clients/{client_id}/rotate": {
        "/admin/api/clients/opc_unknown-client-0000/rotate": statuses(
            404, 403, 403, 403, 403, 403, 403
        ),
    },
    "POST /admin/api/clients/{client_id}/enable": {
        "/admin/api/clients/opc_unknown-client-0000/enable": statuses(
            404, 403, 403, 403, 403, 403, 403
        ),
    },
    "POST /admin/api/clients/{client_id}/delete": {
        "/admin/api/clients/opc_unknown-client-0000/delete": statuses(
            404, 403, 403, 403, 403, 403, 403
        ),
    },
    "POST /admin/api/clients/{client_id}/revoke": {
        "/admin/api/clients/opc_unknown-client-0000/revoke": statuses(
            404, 403, 403, 403, 403, 403, 403
        ),
    },
    # Updates of the installation (ADR 0029): superuser only. Without a release there is
    # nothing to update or restore (409); an unknown request is 404.
    "GET /admin/updates": {"/admin/updates": statuses(200, 403, 403, 403, 403, 403, 403)},
    "GET /admin/updates/progress": {
        "/admin/updates/progress": statuses(200, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/updates/check": {
        "/admin/updates/check": statuses(409, 403, 403, 403, 403, 403, 403)
    },
    "POST /admin/updates/update": {
        "/admin/updates/update": statuses(409, 403, 403, 403, 403, 403, 403)
    },
    "POST /admin/updates/restore": {
        "/admin/updates/restore": statuses(409, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/updates/requests/{request_id}/cancel": {
        "/admin/updates/requests/999999/cancel": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/updates/snooze": {
        "/admin/updates/snooze": statuses(303, 403, 403, 403, 403, 403, 403)
    },
    # Maintenance mode (ADR 0038): superuser only. An empty form has no reason (422); switching
    # off what is off just returns to the page.
    "GET /admin/maintenance": {"/admin/maintenance": statuses(200, 403, 403, 403, 403, 403, 403)},
    "POST /admin/maintenance/on": {
        "/admin/maintenance/on": statuses(422, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/maintenance/off": {
        "/admin/maintenance/off": statuses(303, 403, 403, 403, 403, 403, 403),
    },
    # Message channels (SMTP, SMS gateway) per organisation: secrets, so superuser only.
    "GET /admin/organisations/{org}/channels": {
        "/admin/organisations/demo/channels": statuses(200, 403, 403, 403, 403, 403, 403),
        "/admin/organisations/andere/channels": statuses(200, 403, 403, 403, 403, 403, 403),
        "/admin/organisations/nope/channels": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/channels/email": {
        "/admin/organisations/demo/channels/email": statuses(422, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/channels/email/test": {
        "/admin/organisations/demo/channels/email/test": statuses(
            422, 403, 403, 403, 403, 403, 403
        ),
    },
    # ADR 0073: the sender domain check; without a sender there is nothing to check (422).
    "POST /admin/organisations/{org}/channels/email/domain": {
        "/admin/organisations/demo/channels/email/domain": statuses(
            422, 403, 403, 403, 403, 403, 403
        ),
    },
    "POST /admin/organisations/{org}/channels/sms": {
        "/admin/organisations/demo/channels/sms": statuses(422, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/channels/{channel}/remove": {
        "/admin/organisations/demo/channels/sms/remove": statuses(
            409, 403, 403, 403, 403, 403, 403
        ),
        "/admin/organisations/demo/channels/fax/remove": statuses(
            404, 403, 403, 403, 403, 403, 403
        ),
    },
    # Group creation: organisation admins (in their own organisation) and the superuser.
    "GET /admin/groups/new": {"/admin/groups/new": statuses(200, 200, 403, 200, 403, 403, 403)},
    "POST /admin/groups": {"/admin/groups": statuses(422, 422, 403, 422, 403, 403, 403)},
    "GET /admin/groups/{slug}": {
        "/admin/groups/demo": statuses(200, 200, 200, 403, 200, 200, 403),
        "/admin/groups/andere-team": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    # Group settings (incl. routing to the WxCC queue): not for group admins.
    "GET /admin/groups/{slug}/settings": {
        "/admin/groups/demo/settings": statuses(200, 200, 403, 403, 403, 403, 403),
        "/admin/groups/andere-team/settings": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/settings": {
        "/admin/groups/demo/settings": statuses(422, 422, 403, 403, 403, 403, 403),
        "/admin/groups/andere-team/settings": statuses(422, 403, 403, 422, 403, 403, 403),
    },
    # ADR 0084: archiving needs group.delete (superuser, org admin); an empty confirmation is
    # refused (422).
    "GET /admin/groups/{slug}/settings/archive": {
        "/admin/groups/demo/settings/archive": statuses(200, 200, 403, 403, 403, 403, 403),
        "/admin/groups/andere-team/settings/archive": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/settings/archive": {
        "/admin/groups/demo/settings/archive": statuses(422, 422, 403, 403, 403, 403, 403),
        "/admin/groups/andere-team/settings/archive": statuses(422, 403, 403, 422, 403, 403, 403),
    },
    # ADR 0055: the booking form is part of the group's settings (group.edit). An empty add is
    # refused (422); an unknown field sends back to the list (303).
    "GET /admin/groups/{slug}/settings/form": {
        "/admin/groups/demo/settings/form": statuses(200, 200, 403, 403, 403, 403, 403),
        "/admin/groups/andere-team/settings/form": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/settings/form": {
        "/admin/groups/demo/settings/form": statuses(422, 422, 403, 403, 403, 403, 403),
        "/admin/groups/andere-team/settings/form": statuses(422, 403, 403, 422, 403, 403, 403),
    },
    "GET /admin/groups/{slug}/settings/form/{key}": {
        "/admin/groups/demo/settings/form/nope": statuses(303, 303, 403, 403, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/settings/form/{key}": {
        "/admin/groups/demo/settings/form/nope": statuses(303, 303, 403, 403, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/settings/form/{key}/move": {
        "/admin/groups/demo/settings/form/nope/move": statuses(303, 303, 403, 403, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/settings/form/{key}/retire": {
        "/admin/groups/andere-team/settings/form/nope/retire": statuses(
            303, 403, 403, 303, 403, 403, 403
        ),
    },
    # "Koppeling controleren": whoever edits the group's settings (incl. routing) may check them.
    "POST /admin/groups/{slug}/check": {
        "/admin/groups/demo/check": statuses(303, 303, 403, 403, 403, 403, 403),
        "/admin/groups/andere-team/check": statuses(303, 403, 403, 303, 403, 403, 403),
    },
    # Staffing blocks and staffing exceptions: group admins too, for their own groups.
    "GET /admin/groups/{slug}/staffing": {
        "/admin/groups/demo/staffing": statuses(200, 200, 200, 403, 403, 403, 403),
        "/admin/groups/andere-team/staffing": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/staffing": {
        "/admin/groups/demo/staffing": statuses(422, 422, 422, 403, 403, 403, 403),
        "/admin/groups/andere-team/staffing": statuses(422, 403, 403, 422, 403, 403, 403),
    },
    "GET /admin/groups/{slug}/staffing/{rule_id}": {
        "/admin/groups/demo/staffing/999999": statuses(404, 404, 404, 403, 403, 403, 403),
        "/admin/groups/andere-team/staffing/999999": statuses(404, 403, 403, 404, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/staffing/{rule_id}": {
        "/admin/groups/demo/staffing/999999": statuses(422, 422, 422, 403, 403, 403, 403),
        "/admin/groups/andere-team/staffing/999999": statuses(422, 403, 403, 422, 403, 403, 403),
    },
    "GET /admin/groups/{slug}/staffing/{rule_id}/split": {
        "/admin/groups/demo/staffing/999999/split": statuses(404, 404, 404, 403, 403, 403, 403),
        "/admin/groups/andere-team/staffing/999999/split": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "POST /admin/groups/{slug}/staffing/{rule_id}/split": {
        "/admin/groups/demo/staffing/999999/split": statuses(404, 404, 404, 403, 403, 403, 403),
        "/admin/groups/andere-team/staffing/999999/split": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "POST /admin/groups/{slug}/staffing/{rule_id}/delete": {
        "/admin/groups/demo/staffing/999999/delete": statuses(404, 404, 404, 403, 403, 403, 403),
        "/admin/groups/andere-team/staffing/999999/delete": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    # The old opening-hours paths (before ADR 0027) redirect permanently, behind the same check.
    "GET /admin/groups/{slug}/hours": {
        "/admin/groups/demo/hours": statuses(301, 301, 301, 403, 403, 403, 403),
        "/admin/groups/andere-team/hours": statuses(301, 403, 403, 301, 403, 403, 403),
    },
    "GET /admin/groups/{slug}/hours/{rule_id}": {
        "/admin/groups/demo/hours/999999": statuses(301, 301, 301, 403, 403, 403, 403),
        "/admin/groups/andere-team/hours/999999": statuses(301, 403, 403, 301, 403, 403, 403),
    },
    "GET /admin/groups/{slug}/exceptions": {
        "/admin/groups/demo/exceptions": statuses(200, 200, 200, 403, 403, 403, 403),
        "/admin/groups/andere-team/exceptions": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/exceptions": {
        "/admin/groups/demo/exceptions": statuses(422, 422, 422, 403, 403, 403, 403),
        "/admin/groups/andere-team/exceptions": statuses(422, 403, 403, 422, 403, 403, 403),
    },
    # Reminder rules: organisation admins and the superuser, not group admins (HANDOVER §6).
    # ADR 0084 step 5: a staffing exception and a reminder rule are changed on their own pages,
    # under the permission of their list.
    "GET /admin/groups/{slug}/exceptions/{override_id}": {
        "/admin/groups/demo/exceptions/999999": statuses(404, 404, 404, 403, 403, 403, 403),
        "/admin/groups/andere-team/exceptions/999999": statuses(404, 403, 403, 404, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/exceptions/{override_id}": {
        "/admin/groups/demo/exceptions/999999": statuses(422, 422, 422, 403, 403, 403, 403),
        "/admin/groups/andere-team/exceptions/999999": statuses(422, 403, 403, 422, 403, 403, 403),
    },
    "GET /admin/groups/{slug}/reminders/{channel}/{minutes}": {
        "/admin/groups/demo/reminders/email/999999": statuses(404, 404, 403, 403, 403, 403, 403),
        "/admin/groups/andere-team/reminders/email/999999": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "POST /admin/groups/{slug}/reminders/{channel}/{minutes}": {
        "/admin/groups/demo/reminders/email/999999": statuses(422, 422, 403, 403, 403, 403, 403),
        "/admin/groups/andere-team/reminders/email/999999": statuses(
            422, 403, 403, 422, 403, 403, 403
        ),
    },
    "GET /admin/groups/{slug}/reminders": {
        "/admin/groups/demo/reminders": statuses(200, 200, 403, 403, 403, 403, 403),
        "/admin/groups/andere-team/reminders": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/reminders": {
        "/admin/groups/demo/reminders": statuses(422, 422, 403, 403, 403, 403, 403),
        "/admin/groups/andere-team/reminders": statuses(422, 403, 403, 422, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/reminders/{channel}/{minutes}/delete": {
        "/admin/groups/demo/reminders/sms/999/delete": statuses(404, 404, 403, 403, 403, 403, 403),
        "/admin/groups/andere-team/reminders/sms/999/delete": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    # Message texts: group admins too, for their own groups ("mail texts", HANDOVER §6).
    "GET /admin/groups/{slug}/messages": {
        "/admin/groups/demo/messages": statuses(303, 303, 303, 403, 403, 403, 403),
        "/admin/groups/andere-team/messages": statuses(303, 403, 403, 303, 403, 403, 403),
    },
    "GET /admin/groups/{slug}/messages/{text_locale}": {
        "/admin/groups/demo/messages/nl": statuses(200, 200, 200, 403, 403, 403, 403),
        "/admin/groups/demo/messages/en": statuses(200, 200, 200, 403, 403, 403, 403),
        "/admin/groups/demo/messages/de": statuses(404, 404, 404, 403, 403, 403, 403),
        "/admin/groups/andere-team/messages/nl": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/messages/{text_locale}": {
        "/admin/groups/demo/messages/nl": statuses(303, 303, 303, 403, 403, 403, 403),
        "/admin/groups/andere-team/messages/nl": statuses(303, 403, 403, 303, 403, 403, 403),
    },
    # Agenda and timeline: bookings.view, also for group admins on their own groups.
    "GET /admin/groups/{slug}/agenda": {
        "/admin/groups/demo/agenda": statuses(200, 200, 200, 403, 403, 403, 403),
        "/admin/groups/andere-team/agenda": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    # ADR 0010, 0072: the fragment the agenda polls carries the page's permission and scope.
    "GET /admin/groups/{slug}/agenda/live": {
        "/admin/groups/demo/agenda/live": statuses(200, 200, 200, 403, 403, 403, 403),
        "/admin/groups/andere-team/agenda/live": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    # ADR 0044: a planner's changes; an unknown booking is a 404, an empty form a 400.
    "GET /admin/groups/{slug}/agenda/bookings/{booking_id}/move": {
        "/admin/groups/demo/agenda/bookings/999999/move": statuses(
            404, 404, 404, 403, 403, 403, 403
        ),
        "/admin/groups/andere-team/agenda/bookings/999999/move": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "POST /admin/groups/{slug}/agenda/bookings/{booking_id}/move": {
        "/admin/groups/demo/agenda/bookings/999999/move": statuses(
            400, 400, 400, 403, 403, 403, 403
        ),
    },
    "GET /admin/groups/{slug}/agenda/bookings/{booking_id}/cancel": {
        "/admin/groups/demo/agenda/bookings/999999/cancel": statuses(
            404, 404, 404, 403, 403, 403, 403
        ),
    },
    "POST /admin/groups/{slug}/agenda/bookings/{booking_id}/cancel": {
        "/admin/groups/demo/agenda/bookings/999999/cancel": statuses(
            404, 404, 404, 403, 403, 403, 403
        ),
        "/admin/groups/andere-team/agenda/bookings/999999/cancel": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "GET /admin/groups/{slug}/agenda/bookings/{booking_id}/rebook": {
        "/admin/groups/demo/agenda/bookings/999999/rebook": statuses(
            404, 404, 404, 403, 403, 403, 403
        ),
    },
    "POST /admin/groups/{slug}/agenda/bookings/{booking_id}/rebook": {
        "/admin/groups/demo/agenda/bookings/999999/rebook": statuses(
            404, 404, 404, 403, 403, 403, 403
        ),
        "/admin/groups/andere-team/agenda/bookings/999999/rebook": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "GET /admin/groups/{slug}/agenda/slot-rebook": {
        "/admin/groups/demo/agenda/slot-rebook": statuses(400, 400, 400, 403, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/agenda/slot-rebook": {
        "/admin/groups/demo/agenda/slot-rebook": statuses(400, 400, 400, 403, 403, 403, 403),
    },
    "GET /admin/groups/{slug}/agenda/waiting": {
        "/admin/groups/demo/agenda/waiting": statuses(200, 200, 200, 403, 403, 403, 403),
        "/admin/groups/andere-team/agenda/waiting": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/agenda/accept": {
        "/admin/groups/demo/agenda/accept": statuses(400, 400, 400, 403, 403, 403, 403),
    },
    "GET /admin/groups/{slug}/agenda/week": {
        "/admin/groups/demo/agenda/week": statuses(200, 200, 200, 403, 403, 403, 403),
        "/admin/groups/andere-team/agenda/week": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    "GET /admin/groups/{slug}/bookings/{booking_id}": {
        "/admin/groups/demo/bookings/999999": statuses(404, 404, 404, 403, 403, 403, 403),
        "/admin/groups/andere-team/bookings/999999": statuses(404, 403, 403, 404, 403, 403, 403),
    },
    # Simulator (not in production): whoever edits the group; an empty form is refused (400).
    # ADR 0068: plan a failed meeting or work item job again (bookings.manage, fresh login).
    # An empty form names no job: 400 once the matrix allows it.
    "POST /admin/groups/{slug}/bookings/{booking_id}/retry": {
        "/admin/groups/demo/bookings/999999/retry": statuses(400, 400, 400, 403, 403, 403, 403),
        "/admin/groups/andere-team/bookings/999999/retry": statuses(
            400, 403, 403, 400, 403, 403, 403
        ),
    },
    "POST /admin/groups/{slug}/bookings/{booking_id}/simulate": {
        "/admin/groups/demo/bookings/999999/simulate": statuses(400, 400, 403, 403, 403, 403, 403),
        "/admin/groups/andere-team/bookings/999999/simulate": statuses(
            400, 403, 403, 400, 403, 403, 403
        ),
    },
    "POST /admin/groups/{slug}/exceptions/{override_id}/delete": {
        "/admin/groups/demo/exceptions/999999/delete": statuses(404, 404, 404, 403, 403, 403, 403),
        "/admin/groups/andere-team/exceptions/999999/delete": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    # Emergency closure of one group (ADR 0024): the team's own admins and organisation admins.
    "GET /admin/groups/{slug}/closure": {
        "/admin/groups/demo/closure": statuses(200, 200, 200, 403, 403, 403, 403),
        "/admin/groups/andere-team/closure": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/closure": {
        "/admin/groups/demo/closure": statuses(422, 422, 422, 403, 403, 403, 403),
        "/admin/groups/andere-team/closure": statuses(422, 403, 403, 422, 403, 403, 403),
    },
    # Without a closure there is nothing to update (422) or lift (404).
    "POST /admin/groups/{slug}/closure/update": {
        "/admin/groups/demo/closure/update": statuses(422, 422, 422, 403, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/closure/lift": {
        "/admin/groups/demo/closure/lift": statuses(404, 404, 404, 403, 403, 403, 403),
        "/admin/groups/andere-team/closure/lift": statuses(404, 403, 403, 404, 403, 403, 403),
    },
    # The image of a closure (ADR 0037 §3): an empty form misses its alt text (422); without a
    # closure there is nothing to remove (404).
    "POST /admin/groups/{slug}/closure/image": {
        "/admin/groups/demo/closure/image": statuses(422, 422, 422, 403, 403, 403, 403),
        "/admin/groups/andere-team/closure/image": statuses(422, 403, 403, 422, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/closure/image/remove": {
        "/admin/groups/demo/closure/image/remove": statuses(404, 404, 404, 403, 403, 403, 403),
        "/admin/groups/andere-team/closure/image/remove": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "POST /admin/groups/{slug}/closure/reinstate": {
        "/admin/groups/demo/closure/reinstate": statuses(303, 303, 303, 403, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/closure/cancel": {
        "/admin/groups/demo/closure/cancel": statuses(303, 303, 303, 403, 403, 403, 403),
        "/admin/groups/andere-team/closure/cancel": statuses(303, 403, 403, 303, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/closure/bookings/{booking_id}/reinstate": {
        "/admin/groups/demo/closure/bookings/999999/reinstate": statuses(
            404, 404, 404, 403, 403, 403, 403
        ),
    },
    "POST /admin/groups/{slug}/closure/bookings/{booking_id}/cancel": {
        "/admin/groups/demo/closure/bookings/999999/cancel": statuses(
            404, 404, 404, 403, 403, 403, 403
        ),
        "/admin/groups/andere-team/closure/bookings/999999/cancel": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "GET /admin/groups/{slug}/closure/bookings/{booking_id}/move": {
        "/admin/groups/demo/closure/bookings/999999/move": statuses(
            404, 404, 404, 403, 403, 403, 403
        ),
    },
    # An empty move form has no slot: 400 before anything is looked up.
    "POST /admin/groups/{slug}/closure/bookings/{booking_id}/move": {
        "/admin/groups/demo/closure/bookings/999999/move": statuses(
            400, 400, 400, 403, 403, 403, 403
        ),
    },
    # Emergency closure of a whole organisation: organisation admins and the superuser.
    # ADR 0047 §4: the old closures list redirects permanently to the organisation's tab.
    "GET /admin/closures": {"/admin/closures": statuses(308, 308, 403, 308, 403, 403, 403)},
    "GET /admin/organisations/{org}/closure": {
        "/admin/organisations/demo/closure": statuses(200, 200, 403, 403, 403, 403, 403),
        "/admin/organisations/andere/closure": statuses(200, 403, 403, 200, 403, 403, 403),
        "/admin/organisations/nope/closure": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/closure": {
        "/admin/organisations/demo/closure": statuses(422, 422, 403, 403, 403, 403, 403),
        "/admin/organisations/andere/closure": statuses(422, 403, 403, 422, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/closure/lift": {
        "/admin/organisations/demo/closure/lift": statuses(303, 303, 403, 403, 403, 403, 403),
    },
    # Closure scenarios (ADR 0037 §4): organisation ones with organisation.closure, a
    # group's own with closure.manage. Empty forms are refused (422), unknown ids 404.
    "GET /admin/organisations/{org}/closure/scenarios": {
        "/admin/organisations/demo/closure/scenarios": statuses(200, 200, 403, 403, 403, 403, 403),
        "/admin/organisations/andere/closure/scenarios": statuses(
            200, 403, 403, 200, 403, 403, 403
        ),
    },
    "POST /admin/organisations/{org}/closure/scenarios": {
        "/admin/organisations/demo/closure/scenarios": statuses(422, 422, 403, 403, 403, 403, 403),
        "/admin/organisations/andere/closure/scenarios": statuses(
            422, 403, 403, 422, 403, 403, 403
        ),
    },
    "GET /admin/organisations/{org}/closure/scenarios/{scenario_id}": {
        "/admin/organisations/demo/closure/scenarios/999999": statuses(
            404, 404, 403, 403, 403, 403, 403
        ),
        "/admin/organisations/andere/closure/scenarios/999999": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "POST /admin/organisations/{org}/closure/scenarios/{scenario_id}": {
        "/admin/organisations/demo/closure/scenarios/999999": statuses(
            404, 404, 403, 403, 403, 403, 403
        ),
        "/admin/organisations/andere/closure/scenarios/999999": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "POST /admin/organisations/{org}/closure/scenarios/{scenario_id}/delete": {
        "/admin/organisations/demo/closure/scenarios/999999/delete": statuses(
            404, 404, 403, 403, 403, 403, 403
        ),
        "/admin/organisations/andere/closure/scenarios/999999/delete": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "GET /admin/organisations/{org}/closure/scenarios/{scenario_id}/contacts": {
        "/admin/organisations/demo/closure/scenarios/999999/contacts": statuses(
            404, 404, 403, 403, 403, 403, 403
        ),
        "/admin/organisations/andere/closure/scenarios/999999/contacts": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "POST /admin/organisations/{org}/closure/scenarios/{scenario_id}/contacts": {
        "/admin/organisations/demo/closure/scenarios/999999/contacts": statuses(
            404, 404, 403, 403, 403, 403, 403
        ),
        "/admin/organisations/andere/closure/scenarios/999999/contacts": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "POST /admin/organisations/{org}/closure/scenarios/{scenario_id}/image": {
        "/admin/organisations/demo/closure/scenarios/999999/image": statuses(
            404, 404, 403, 403, 403, 403, 403
        ),
        "/admin/organisations/andere/closure/scenarios/999999/image": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "POST /admin/organisations/{org}/closure/scenarios/{scenario_id}/image/remove": {
        "/admin/organisations/demo/closure/scenarios/999999/image/remove": statuses(
            404, 404, 403, 403, 403, 403, 403
        ),
        "/admin/organisations/andere/closure/scenarios/999999/image/remove": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "GET /admin/organisations/{org}/closure/scenarios/{scenario_id}/image": {
        "/admin/organisations/demo/closure/scenarios/999999/image": statuses(
            404, 404, 403, 403, 403, 403, 403
        ),
        "/admin/organisations/andere/closure/scenarios/999999/image": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "GET /admin/groups/{slug}/closure/scenarios": {
        "/admin/groups/demo/closure/scenarios": statuses(200, 200, 200, 403, 403, 403, 403),
        "/admin/groups/andere-team/closure/scenarios": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/closure/scenarios": {
        "/admin/groups/demo/closure/scenarios": statuses(422, 422, 422, 403, 403, 403, 403),
        "/admin/groups/andere-team/closure/scenarios": statuses(422, 403, 403, 422, 403, 403, 403),
    },
    "GET /admin/groups/{slug}/closure/scenarios/{scenario_id}": {
        "/admin/groups/demo/closure/scenarios/999999": statuses(404, 404, 404, 403, 403, 403, 403),
        "/admin/groups/andere-team/closure/scenarios/999999": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "POST /admin/groups/{slug}/closure/scenarios/{scenario_id}": {
        "/admin/groups/demo/closure/scenarios/999999": statuses(404, 404, 404, 403, 403, 403, 403),
        "/admin/groups/andere-team/closure/scenarios/999999": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "POST /admin/groups/{slug}/closure/scenarios/{scenario_id}/delete": {
        "/admin/groups/demo/closure/scenarios/999999/delete": statuses(
            404, 404, 404, 403, 403, 403, 403
        ),
        "/admin/groups/andere-team/closure/scenarios/999999/delete": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "GET /admin/groups/{slug}/closure/scenarios/{scenario_id}/contacts": {
        "/admin/groups/demo/closure/scenarios/999999/contacts": statuses(
            404, 404, 404, 403, 403, 403, 403
        ),
        "/admin/groups/andere-team/closure/scenarios/999999/contacts": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "POST /admin/groups/{slug}/closure/scenarios/{scenario_id}/contacts": {
        "/admin/groups/demo/closure/scenarios/999999/contacts": statuses(
            404, 404, 404, 403, 403, 403, 403
        ),
        "/admin/groups/andere-team/closure/scenarios/999999/contacts": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "POST /admin/groups/{slug}/closure/scenarios/{scenario_id}/image": {
        "/admin/groups/demo/closure/scenarios/999999/image": statuses(
            404, 404, 404, 403, 403, 403, 403
        ),
        "/admin/groups/andere-team/closure/scenarios/999999/image": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "POST /admin/groups/{slug}/closure/scenarios/{scenario_id}/image/remove": {
        "/admin/groups/demo/closure/scenarios/999999/image/remove": statuses(
            404, 404, 404, 403, 403, 403, 403
        ),
        "/admin/groups/andere-team/closure/scenarios/999999/image/remove": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    "GET /admin/groups/{slug}/closure/scenarios/{scenario_id}/image": {
        "/admin/groups/demo/closure/scenarios/999999/image": statuses(
            404, 404, 404, 403, 403, 403, 403
        ),
        "/admin/groups/andere-team/closure/scenarios/999999/image": statuses(
            404, 403, 403, 404, 403, 403, 403
        ),
    },
    # Alternative contact options of the organisation (ADR 0037): with organisation.closure.
    # An empty form removes nothing that is not there, so an allowed POST redirects (303).
    # ADR 0035: the websites allowed to embed the organisation's groups, embedding.manage.
    # An empty form removes nothing that is not there, so an allowed POST redirects (303).
    # ADR 0047 §4: the old embedding list redirects permanently to the organisation's sub-tab.
    "GET /admin/embedding": {"/admin/embedding": statuses(308, 308, 403, 308, 403, 403, 403)},
    "GET /admin/organisations/{org}/embedding": {
        "/admin/organisations/demo/embedding": statuses(200, 200, 403, 403, 403, 403, 403),
        "/admin/organisations/andere/embedding": statuses(200, 403, 403, 200, 403, 403, 403),
        "/admin/organisations/nope/embedding": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/embedding": {
        "/admin/organisations/demo/embedding": statuses(303, 303, 403, 403, 403, 403, 403),
        "/admin/organisations/andere/embedding": statuses(303, 403, 403, 303, 403, 403, 403),
    },
    # ADR 0085 step 4: the log destinations of the platform. The list and a destination are
    # for the superuser and the auditor (logging.view); every change is the superuser's
    # (logging.manage is locked). No destination exists, so an id answers 404 to whoever may look.
    "GET /admin/logging": {"/admin/logging": statuses(200, 403, 403, 403, 200, 403, 403)},
    "GET /admin/logging/new": {"/admin/logging/new": statuses(200, 403, 403, 403, 403, 403, 403)},
    "POST /admin/logging": {"/admin/logging": statuses(422, 403, 403, 403, 403, 403, 403)},
    "GET /admin/logging/{destination_id}": {
        "/admin/logging/999": statuses(404, 403, 403, 403, 404, 403, 403)
    },
    "POST /admin/logging/{destination_id}": {
        "/admin/logging/999": statuses(404, 403, 403, 403, 403, 403, 403)
    },
    "POST /admin/logging/{destination_id}/enable": {
        "/admin/logging/999/enable": statuses(404, 403, 403, 403, 403, 403, 403)
    },
    "POST /admin/logging/{destination_id}/delete": {
        "/admin/logging/999/delete": statuses(404, 403, 403, 403, 403, 403, 403)
    },
    "POST /admin/logging/{destination_id}/test": {
        "/admin/logging/999/test": statuses(404, 403, 403, 403, 403, 403, 403)
    },
    # ADR 0083 step 2: the Login sub-tab. The status is for the organisation's own admins
    # and auditors; registering and changing is the superuser's (identity_providers.manage is
    # locked). No provider is registered, so a key answers 404 to whoever may look.
    "GET /admin/organisations/{org}/login": {
        "/admin/organisations/demo/login": statuses(200, 200, 403, 403, 200, 403, 403),
        "/admin/organisations/andere/login": statuses(200, 403, 403, 200, 403, 403, 403),
        "/admin/organisations/nope/login": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "GET /admin/organisations/{org}/login/new": {
        "/admin/organisations/demo/login/new": statuses(200, 403, 403, 403, 403, 403, 403),
        "/admin/organisations/nope/login/new": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/login": {
        "/admin/organisations/demo/login": statuses(422, 403, 403, 403, 403, 403, 403),
    },
    "GET /admin/organisations/{org}/login/{idp_key}": {
        "/admin/organisations/demo/login/gem-x": statuses(404, 404, 403, 403, 404, 403, 403),
        "/admin/organisations/andere/login/gem-x": statuses(404, 403, 403, 404, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/login/{idp_key}": {
        "/admin/organisations/demo/login/gem-x": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/login/{idp_key}/enable": {
        "/admin/organisations/demo/login/gem-x/enable": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/login/{idp_key}/delete": {
        "/admin/organisations/demo/login/gem-x/delete": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/login/{idp_key}/rotate-key": {
        "/admin/organisations/demo/login/gem-x/rotate-key": statuses(
            404, 403, 403, 403, 403, 403, 403
        ),
    },
    "POST /admin/organisations/{org}/login/{idp_key}/test": {
        "/admin/organisations/demo/login/gem-x/test": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "GET /admin/organisations/{org}/contacts": {
        "/admin/organisations/demo/contacts": statuses(200, 200, 403, 403, 403, 403, 403),
        "/admin/organisations/andere/contacts": statuses(200, 403, 403, 200, 403, 403, 403),
        "/admin/organisations/nope/contacts": statuses(404, 403, 403, 403, 403, 403, 403),
    },
    "POST /admin/organisations/{org}/contacts": {
        "/admin/organisations/demo/contacts": statuses(303, 303, 403, 403, 403, 403, 403),
        "/admin/organisations/andere/contacts": statuses(303, 403, 403, 303, 403, 403, 403),
    },
    # ADR 0043: the own queue needs queue.work (agents, group and organisation admins). The
    # demo group dispatches to the contact center, so allowed requests find no queue (404).
    "GET /admin/groups/{slug}/queue": {
        "/admin/groups/demo/queue": statuses(404, 404, 404, 403, 403, 404, 403),
        "/admin/groups/andere-team/queue": statuses(404, 403, 403, 404, 403, 403, 403),
    },
    # ADR 0072: the fragments the live queue polls carry the same permission as the page.
    "GET /admin/groups/{slug}/queue/live": {
        "/admin/groups/demo/queue/live": statuses(404, 404, 404, 403, 403, 404, 403),
        "/admin/groups/andere-team/queue/live": statuses(404, 403, 403, 404, 403, 403, 403),
    },
    "GET /admin/groups/{slug}/queue/{booking_id}/live": {
        "/admin/groups/demo/queue/999999/live": statuses(404, 404, 404, 403, 403, 404, 403),
    },
    "POST /admin/groups/{slug}/queue/next": {
        "/admin/groups/demo/queue/next": statuses(404, 404, 404, 403, 403, 404, 403),
        "/admin/groups/andere-team/queue/next": statuses(404, 403, 403, 404, 403, 403, 403),
    },
    "GET /admin/groups/{slug}/queue/{booking_id}": {
        "/admin/groups/demo/queue/999999": statuses(404, 404, 404, 403, 403, 404, 403),
    },
    "POST /admin/groups/{slug}/queue/{booking_id}/claim": {
        "/admin/groups/demo/queue/999999/claim": statuses(404, 404, 404, 403, 403, 404, 403),
    },
    "POST /admin/groups/{slug}/queue/{booking_id}/release": {
        "/admin/groups/demo/queue/999999/release": statuses(404, 404, 404, 403, 403, 404, 403),
    },
    # A group's own contact block: closure.manage, like the rest of its closure page.
    "GET /admin/groups/{slug}/closure/contacts": {
        "/admin/groups/demo/closure/contacts": statuses(200, 200, 200, 403, 403, 403, 403),
        "/admin/groups/andere-team/closure/contacts": statuses(200, 403, 403, 200, 403, 403, 403),
    },
    "POST /admin/groups/{slug}/closure/contacts": {
        "/admin/groups/demo/closure/contacts": statuses(303, 303, 303, 403, 403, 403, 403),
        "/admin/groups/andere-team/closure/contacts": statuses(303, 403, 403, 303, 403, 403, 403),
    },
}

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
