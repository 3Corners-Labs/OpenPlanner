"""Disabled buttons during maintenance (ADR 0038, MM-2): from the matrix, on every admin page."""

import html
import re

import pytest

from app.admin.frozen_forms import BAR_ID, LEFT_ALONE, freeze_forms, requirement_for
from app.domain.maintenance import MaintenanceReason
from app.domain.maintenance_service import MaintenanceService
from tests.admin.conftest import ORG_ADMIN, SUPER, Site
from tests.admin.test_authorization_matrix import EXPECTED, ROLES

FROZEN = f'disabled aria-describedby="{BAR_ID}"'
FORM = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.DOTALL)

PAGE = """<form method="get" action="/admin/audit"><button type="submit">Filter</button></form>
<form class="card" method="post" action="/admin/a?x=1&amp;y=2">
<button type="submit" class="b">Save</button><button type="submit">Again</button></form>
<form method="post" action="/admin/b"><button type="submit">Check</button></form>
<form method="post" action="/admin/logout"><button type="submit">Log out</button></form>"""


def test_only_the_refused_post_forms_are_frozen() -> None:
    seen: list[str] = []

    def writable(action: str) -> bool:
        seen.append(action)
        return action == "/admin/b"

    frozen = freeze_forms(PAGE, writable)

    assert frozen.count(FROZEN) == 2
    assert '<button type="submit">Filter</button>' in frozen
    assert '<button type="submit">Check</button>' in frozen
    assert '<button type="submit">Log out</button>' in frozen
    assert seen == ["/admin/a?x=1&amp;y=2", "/admin/b"]


def maintenance(site: Site) -> MaintenanceService:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    found: MaintenanceService = app.state.maintenance_service
    return found


def forms(page: str) -> list[tuple[str, bool]]:
    """Every POST form of ``page``: its action and whether its buttons are frozen."""
    found = []
    for attributes, body in FORM.findall(page):
        if 'method="post"' not in attributes:
            continue
        action = re.search(r'action="([^"]*)"', attributes)
        assert action is not None
        found.append((html.unescape(action.group(1)), FROZEN in body))
    return found


async def test_superuser_keeps_integrations_and_the_check_but_not_the_rest(site: Site) -> None:
    await maintenance(site).switch_on_host("host-cli", MaintenanceReason.OTHER)
    await site.login_as(SUPER)

    channels = forms((await site.client.get("/admin/organisations/demo/channels")).text)
    settings = dict(forms((await site.client.get("/admin/groups/demo/settings")).text))

    assert channels
    assert not any(frozen for _, frozen in channels)
    assert settings["/admin/groups/demo/settings"]
    assert not settings["/admin/groups/demo/check"]
    assert not settings["/admin/logout"]


async def test_an_organisation_admin_sees_every_change_disabled(site: Site) -> None:
    await maintenance(site).switch_on_host("host-cli", MaintenanceReason.OTHER)
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get("/admin/groups/demo/settings")).text

    assert dict(forms(page)) == {
        "/admin/logout": False,
        "/admin/groups/demo/settings": True,
        "/admin/groups/demo/settings/idp-key": True,
        "/admin/groups/demo/check": True,
    }


async def test_nothing_is_frozen_without_maintenance(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get("/admin/groups/demo/settings")).text

    assert FROZEN not in page


PAGES = [
    (role, path)
    for key, routes in EXPECTED.items()
    if key.startswith("GET ")
    for path, by_role in routes.items()
    for role, status in by_role.items()
    if status == 200 and role in ("superuser", "org_admin (demo)")
]


@pytest.mark.parametrize(("role", "path"), PAGES)
async def test_every_post_form_on_every_page_resolves_to_its_route(
    site: Site, role: str, path: str
) -> None:
    # A form whose action matches no route would be frozen by default; none may rely on that.
    await maintenance(site).switch_on_host("host-cli", MaintenanceReason.OTHER)
    await site.login_as(ROLES[role])
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    page = (await site.client.get(path)).text

    for action, _ in forms(page):
        target = action.split("?", 1)[0]
        assert target in LEFT_ALONE or requirement_for(app.routes, target), (path, action)
