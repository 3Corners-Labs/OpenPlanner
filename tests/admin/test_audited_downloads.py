"""The audited downloads refuse a request that another site makes (ASVS V3.5.3, ADR 0053).

Two admin downloads write an audit event when they are opened: the audit log export
(``audit.export``, ADR 0040) and the identity provider mapping (``idp_mapping.exported``,
ADR 0049 point 17). The admin cookie is ``SameSite=Lax``, so it travels on a top-level
navigation from any site: without a guard, a link on another site makes a signed-in admin's
browser download the file and puts an export in their name in the audit log. Both depend on
``SameOrigin``, like every form post of the portal. The sweep at the end opens every admin
``GET`` and keeps ``AUDITED_DOWNLOADS`` complete.
"""

from collections import Counter

import pytest

from app.domain.audit import Outcome
from app.domain.audit_export import EXPORT_EVENT
from app.domain.idp_mapping import EXPORTED_EVENT
from app.domain.security_log import CROSS_SITE, REQUEST_REJECTED
from tests.admin.conftest import SUPER, Site
from tests.support.admin_site import audit_events
from tests.support.authorization_matrix import GET_PATHS

AUDITED_DOWNLOADS = {
    "/admin/audit/export.csv": EXPORT_EVENT,
    "/admin/idp-mapping/export.txt": EXPORTED_EVENT,
}
"""Every admin ``GET`` that writes an audit event when opened, with that event's action."""


@pytest.mark.parametrize("path", AUDITED_DOWNLOADS)
@pytest.mark.parametrize("fetch_site", ["cross-site", "same-site"])
async def test_a_download_another_site_asks_for_is_refused_and_not_audited(
    site: Site, path: str, fetch_site: str
) -> None:
    await site.login_as(SUPER)

    response = await site.client.get(path, headers={"sec-fetch-site": fetch_site})

    assert response.status_code == 403
    assert "content-disposition" not in response.headers
    events = [(action, outcome, reason) for _, action, outcome, reason in await audit_events(site)]
    assert (REQUEST_REJECTED, Outcome.DENIED, CROSS_SITE) in events
    assert AUDITED_DOWNLOADS[path] not in {action for action, _, _ in events}


@pytest.mark.parametrize("path", AUDITED_DOWNLOADS)
@pytest.mark.parametrize("fetch_site", ["same-origin", "none"])
async def test_a_download_from_the_portal_or_the_address_bar_goes_ahead_and_is_audited(
    site: Site, path: str, fetch_site: str
) -> None:
    # ``same-origin``: the link on the page. ``none``: typed, bookmarked or opened from another
    # program; no page can set it, so the admin opened the address themselves.
    await site.login_as(SUPER)

    response = await site.client.get(path, headers={"sec-fetch-site": fetch_site})

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment; ")
    events = [(actor, action, outcome) for actor, action, outcome, _ in await audit_events(site)]
    assert (SUPER.subject, AUDITED_DOWNLOADS[path], Outcome.SUCCESS) in events


async def _written(site: Site, path: str, fetch_site: str) -> list[str]:
    """Return the actions a ``GET`` of ``path`` adds to the audit log, refusals left out.

    A refusal (``denied``) records what the browser asked for, not something the admin did.
    """
    before = Counter(await audit_events(site))
    await site.client.get(path, headers={"sec-fetch-site": fetch_site})
    added = Counter(await audit_events(site)) - before
    return sorted(action for _, action, outcome, _ in added.elements() if outcome != Outcome.DENIED)


@pytest.mark.matrix
@pytest.mark.parametrize("path", GET_PATHS)
async def test_no_admin_get_writes_an_audit_event_when_another_site_asks_for_it(
    site: Site, path: str
) -> None:
    # The superuser opens every admin page. From another site nothing is written; from the
    # portal only the listed downloads write their event, so a new one joins the list (and the
    # threat model's table of GET routes with an effect) and gets the guard.
    await site.login_as(SUPER)

    assert await _written(site, path, "cross-site") == []
    assert await _written(site, path, "same-origin") == (
        [AUDITED_DOWNLOADS[path]] if path in AUDITED_DOWNLOADS else []
    )
