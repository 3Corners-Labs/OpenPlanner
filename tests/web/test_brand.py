"""The product mark and favicons (ADR 0060): safe files, linked from every page."""

import re
from pathlib import Path

import pytest

from app.config import Settings
from app.web.static_assets import ASSETS, STATIC_DIR
from tests.admin.conftest import (
    AGENT,
    AUDITOR,
    GROUP_ADMIN,
    NO_ROLE,
    ORG_ADMIN,
    OUTSIDER,
    SUPER,
    Site,
)
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW

BRAND = STATIC_DIR / "brand"
ROLE_PRINCIPALS = {
    "superuser": SUPER,
    "org_admin": ORG_ADMIN,
    "group_admin": GROUP_ADMIN,
    "org_admin_other": OUTSIDER,
    "auditor": AUDITOR,
    "agent": AGENT,
    "no_role": NO_ROLE,
}
SVGS = sorted(BRAND.glob("*.svg"))
PNGS = sorted(BRAND.glob("*.png"))
EVENT_HANDLER = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
FAVICON_LINKS = (
    f'<link rel="icon" href="{ASSETS.url("brand/favicon.svg")}" type="image/svg+xml">',
    f'<link rel="icon" href="{ASSETS.url("brand/favicon-32.png")}" type="image/png" sizes="32x32">',
    f'<link rel="apple-touch-icon" href="{ASSETS.url("brand/apple-touch-icon.png")}">',
)
MARK = (
    f'<img class="product-footer__mark" src="{ASSETS.url("brand/mark.svg")}" alt="" '
    'width="20" height="20">'
)


def test_the_brand_files_of_the_adr_ship() -> None:
    assert [p.name for p in SVGS] == ["favicon.svg", "mark-mono.svg", "mark.svg"]
    assert [p.name for p in PNGS] == ["apple-touch-icon.png", "favicon-32.png", "mark-48.png"]


@pytest.mark.parametrize("path", SVGS, ids=lambda p: p.name)
def test_svgs_carry_no_script_handler_or_external_reference(path: Path) -> None:
    text = path.read_text()

    assert "<script" not in text.lower()
    assert not EVENT_HANDLER.search(text)
    assert "href" not in text.lower()  # no xlink:href, no <a>, no <use> from elsewhere
    assert "<metadata" not in text
    assert "<foreignobject" not in text.lower()
    assert "http" not in text.replace('xmlns="http://www.w3.org/2000/svg"', "")


@pytest.mark.parametrize("path", PNGS, ids=lambda p: p.name)
def test_pngs_are_pngs(path: Path) -> None:
    assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


async def test_the_favicons_are_served(site: Site) -> None:
    for name, media_type in (
        ("favicon.svg", "image/svg+xml"),
        ("favicon-32.png", "image/png"),
        ("apple-touch-icon.png", "image/png"),
        ("mark.svg", "image/svg+xml"),
    ):
        response = await site.client.get(f"/static/brand/{name}")
        assert response.status_code == 200, name
        assert response.headers["content-type"].startswith(media_type), name


async def test_a_citizen_page_links_the_favicons_and_shows_the_footer(site: Site) -> None:
    page = (await site.client.get("/g/demo?lang=en")).text

    for link in FAVICON_LINKS:
        assert link in page
    footer = page.split('<footer class="site-footer">', 1)[1].split("</footer>", 1)[0]
    assert MARK in footer
    assert "Appointments via" in footer
    assert '<span class="product-footer__name">OpenPlanner</span>' in footer
    # The organisation's own header comes first (ADR 0060 §6).
    assert page.index('<header class="site-header') < page.index('<footer class="site-footer">')


async def test_the_footer_speaks_dutch_by_default(site: Site) -> None:
    page = (await site.client.get("/g/demo")).text

    assert "Afspraken via" in page.split('<footer class="site-footer">', 1)[1]


async def test_the_footer_follows_the_organisations_setting(site: Site) -> None:
    await site.login_as(ROLE_PRINCIPALS["org_admin"])
    response = await site.client.post(
        "/admin/organisations/demo/settings/product_footer",
        data={"value": "false"},
        headers={"sec-fetch-site": "same-origin"},
    )
    assert response.status_code == 303
    # The settings screen refreshes the live values at once; citizen pages pick them up within
    # the worker's reload interval (ADR 0042 step 5).
    await site.client.get("/admin/organisations/demo/settings")

    page = (await site.client.get("/g/demo")).text

    assert '<footer class="site-footer">' not in page
    assert ASSETS.url("brand/mark.svg") not in page
    for link in FAVICON_LINKS:  # the favicon is product identity, never a setting
        assert link in page


async def test_the_platform_value_switches_the_footer_off_everywhere(settings: Settings) -> None:
    async with app_client(
        settings.model_copy(update={"product_footer": False}), FixedClock(NOW)
    ) as client:
        page = (await client.get("/")).text

    assert '<footer class="site-footer">' not in page


async def test_pages_without_an_organisation_follow_the_platform_value(
    site: Site,
) -> None:
    home = (await site.client.get("/?lang=en")).text
    missing = await site.client.get("/g/no-such-group")

    assert MARK in home
    assert missing.status_code == 404
    assert MARK in missing.text


@pytest.mark.parametrize("role", list(ROLE_PRINCIPALS))
async def test_the_admin_top_bar_shows_the_mark_for_every_role(site: Site, role: str) -> None:
    await site.login_as(ROLE_PRINCIPALS[role])

    response = await site.client.get("/admin/?lang=en")
    page = response.text

    assert response.status_code in (200, 403), role
    for link in FAVICON_LINKS:
        assert link in page
    top_bar = page.split('<header class="admin-topbar">', 1)[1].split("</header>", 1)[0]
    assert (
        f'<img class="admin-brand__mark" src="{ASSETS.url("brand/mark.svg")}" alt="" '
        'width="26" height="26">' in top_bar
    )
    assert "<span>Administration</span>" in top_bar  # the product name stays text
    assert ">OP<" not in top_bar
    assert '<footer class="site-footer">' not in page  # the citizen footer is not the portal's


async def test_the_portal_has_no_citizen_footer_in_any_theme(site: Site) -> None:
    await site.login_as(ROLE_PRINCIPALS["superuser"])
    for theme in ("light", "dark"):
        page = (await site.client.get(f"/admin/?theme={theme}")).text
        assert f'data-theme="{theme}"' in page
        assert '<footer class="site-footer">' not in page
        assert ASSETS.url("brand/mark.svg") in page
