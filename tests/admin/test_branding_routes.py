"""Branding screens (WP3b): name and colour, logo upload and removal, preview and audit."""

import httpx

from tests.admin.conftest import SAME_ORIGIN, SUPER, Site
from tests.admin.test_admin_flow import audit_events

PAGE = "/admin/organisations/demo/branding"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def _save(site: Site, **data: str) -> httpx.Response:
    return await site.client.post(PAGE, data=data, headers=SAME_ORIGIN)


async def test_superuser_finds_the_organisations_in_the_menu(site: Site) -> None:
    await site.login_as(SUPER)
    overview = (await site.client.get("/admin/")).text
    listing = (await site.client.get("/admin/branding")).text

    assert 'href="/admin/branding"' in overview
    assert f'href="{PAGE}"' in listing
    assert 'href="/admin/organisations/andere/branding"' in listing


async def test_name_and_colour_are_saved_shown_and_audited(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _save(site, display_name="Gemeente Demo", accent_color="#154273")

    assert response.status_code == 303
    assert response.headers["location"] == f"{PAGE}?done=saved"
    page = (await site.client.get(response.headers["location"])).text
    assert "De wijzigingen zijn opgeslagen." in page
    assert 'value="#154273"' in page
    assert 'href="/o/demo/theme.css?v=154273"' in page  # the preview uses the saved theme
    citizen = (await site.client.get("/g/demo")).text
    assert "<span>Gemeente Demo</span>" in citizen
    assert ("sub-super", "branding.manage", "success", "") in [
        (a, b, str(c), d) for a, b, c, d in await audit_events(site)
    ]
    again = await _save(site, display_name="Gemeente Demo", accent_color="#154273")
    assert again.headers["location"] == f"{PAGE}?done=unchanged"


async def test_a_light_colour_is_refused_with_a_linked_message(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _save(site, display_name="", accent_color="#ffbf47")

    assert response.status_code == 422
    assert 'href="#accent_color"' in response.text
    assert 'aria-describedby="accent_color-hint accent_color-error"' in response.text
    assert "4,5:1" in response.text
    assert 'value="#ffbf47"' in response.text  # what the admin entered is kept


async def test_logo_upload_replaces_and_removal_clears(site: Site) -> None:
    await site.login_as(SUPER)

    upload = await site.client.post(
        f"{PAGE}/logo", files={"logo": ("logo.png", PNG, "image/png")}, headers=SAME_ORIGIN
    )
    assert upload.status_code == 303
    assert upload.headers["location"] == f"{PAGE}?done=logo_saved"
    page = (await site.client.get(PAGE)).text
    assert 'src="/o/demo/logo?v=' in page
    assert "Logo verwijderen" in page
    logo = await site.client.get("/o/demo/logo")
    assert logo.content == PNG
    assert logo.headers["content-type"] == "image/png"

    removed = await site.client.post(f"{PAGE}/logo/remove", headers=SAME_ORIGIN)
    assert removed.headers["location"] == f"{PAGE}?done=logo_removed"
    assert (await site.client.get("/o/demo/logo")).status_code == 404


async def test_svg_and_oversized_logos_are_refused(site: Site) -> None:
    await site.login_as(SUPER)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'

    refused = await site.client.post(
        f"{PAGE}/logo", files={"logo": ("logo.png", svg, "image/png")}, headers=SAME_ORIGIN
    )
    huge = await site.client.post(
        f"{PAGE}/logo",
        files={"logo": ("logo.png", PNG + b"\x00" * 300_000, "image/png")},
        headers=SAME_ORIGIN,
    )

    assert refused.status_code == 422
    assert "SVG kan niet" in refused.text
    assert huge.status_code == 413
    assert (await site.client.get("/o/demo/logo")).status_code == 404


async def test_cross_site_branding_posts_are_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.post(
        PAGE,
        data={"display_name": "X", "accent_color": "#000000"},
        headers={"sec-fetch-site": "cross-site"},
    )

    assert response.status_code == 403
