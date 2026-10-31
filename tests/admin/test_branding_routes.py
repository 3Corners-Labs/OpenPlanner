"""Branding screens (WP3b, ADR 0041): name, header, colours and shape, logo, fonts, theme file."""

import httpx

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from app.domain.branding import Branding
from tests.admin.conftest import SAME_ORIGIN, SUPER, Site
from tests.admin.test_admin_flow import audit_events

PAGE = "/admin/organisations/demo/branding"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def _save(site: Site, **data: str) -> httpx.Response:
    return await site.client.post(PAGE, data=data, headers=SAME_ORIGIN)


async def test_superuser_reaches_branding_from_the_organisation_page(site: Site) -> None:
    await site.login_as(SUPER)
    overview = (await site.client.get("/admin/")).text
    organisation = (await site.client.get("/admin/organisations/demo")).text

    # ADR 0047 §4: no branding entry in the menu; the tab on the organisation's page.
    assert 'href="/admin/branding"' not in overview
    assert f'href="{PAGE}"' in organisation
    assert (await site.client.get("/admin/branding")).status_code == 308


async def test_name_and_colour_are_saved_shown_and_audited(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _save(site, display_name="Gemeente Demo", accent_color="#154273")

    assert response.status_code == 303
    assert response.headers["location"] == f"{PAGE}?done=saved"
    page = (await site.client.get(response.headers["location"])).text
    assert "De wijzigingen zijn opgeslagen." in page
    assert 'value="#154273"' in page
    saved = Branding("Gemeente Demo", "#154273")
    assert f'href="/o/demo/theme.css?v={saved.theme_digest}"' in page  # preview: saved theme
    participant = (await site.client.get("/g/demo")).text
    assert "<span>Gemeente Demo</span>" in participant
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


WOFF2 = b"wOF2" + b"\x00" * 64


async def _last_change(site: Site) -> str | None:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        events = await tx.list_audit_events(Scope(everything=True), limit=50)
    return next(
        e.event.detail for e in events if e.event.action == "branding.manage" and e.event.detail
    )


async def _upload(site: Site, path: str, name: str, data: bytes) -> httpx.Response:
    return await site.client.post(
        f"{PAGE}/{path}",
        files={name: ("file", data, "application/octet-stream")},
        headers=SAME_ORIGIN,
    )


async def test_header_colours_and_shape_are_saved_and_reach_the_theme(site: Site) -> None:
    await site.login_as(SUPER)
    form = {
        "display_name": "",
        "accent_color": "#154273",
        "header_style": "accent",
        "color_text": "#0b0c0c",
        "color_page": "#ffffff",
        "radius": "0",
        "font_size": "18",
        "shadow": "none",
    }

    response = await _save(site, **form)

    assert response.status_code == 303
    page = (await site.client.get(PAGE)).text
    assert '<option value="accent" selected>' in page
    assert 'id="radius" name="radius" type="number" value="0"' in page
    theme = (await site.client.get("/o/demo/theme.css")).text
    for line in ("--booking-radius: 0px;", "--booking-font-size: 18px;", "--booking-shadow: none;"):
        assert line in theme
    assert "--booking-color-text: #0B0C0C;" in theme
    assert "--booking-color-page: #FFFFFF;" in theme
    assert "--booking-color-surface" not in theme  # not on the form: left at its default
    assert "site-header--accent" in (await site.client.get("/g/demo")).text
    assert await _last_change(site) == "accent_color,header_style,theme"
    again = await _save(site, **form)
    assert again.headers["location"] == f"{PAGE}?done=unchanged"


async def test_colours_without_contrast_are_refused_and_linked(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _save(site, accent_color="#01689b", color_text="#aaaaaa")

    assert response.status_code == 422
    assert 'href="#color_text"' in response.text
    assert "te weinig contrast" in response.text
    assert 'value="#aaaaaa"' in response.text


async def test_numbers_outside_their_bounds_are_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _save(site, accent_color="#01689b", font_size="12", radius="x")

    assert response.status_code == 422
    assert 'href="#font_size"' in response.text
    assert 'href="#radius"' in response.text


async def test_the_theme_file_downloads_and_uploads_again(site: Site) -> None:
    await site.login_as(SUPER)
    await _save(site, accent_color="#154273", radius="0")

    download = await site.client.get(f"{PAGE}/theme.css")
    assert download.status_code == 200
    assert download.headers["content-disposition"] == 'attachment; filename="theme-demo.css"'
    assert "  --booking-radius: 0px;" in download.text
    assert "  --booking-color-accent: #154273;" in download.text

    edited = download.text.replace("--booking-radius: 0px;", "--booking-radius: 4px;")
    upload = await _upload(site, "theme", "theme_file", edited.encode())

    assert upload.status_code == 303
    assert upload.headers["location"] == f"{PAGE}?done=theme_saved"
    assert "--booking-radius: 4px;" in (await site.client.get("/o/demo/theme.css")).text
    assert await _last_change(site) == "theme_file"


async def test_free_css_in_a_theme_file_is_refused_with_its_line(site: Site) -> None:
    await site.login_as(SUPER)
    evil = (
        b":root {\n  --booking-radius: 0px;\n}\n"
        b"input[value^=a] { background: url(https://x.example/a) }\n"
    )

    response = await _upload(site, "theme", "theme_file", evil)

    assert response.status_code == 422
    assert "Regel 4 van het themabestand" in response.text
    assert 'href="#theme_file"' in response.text
    assert 'aria-describedby="theme_file-hint theme_file-error"' in response.text
    assert "x.example" not in (await site.client.get("/o/demo/theme.css")).text


async def test_a_theme_file_without_contrast_names_the_token(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _upload(site, "theme", "theme_file", b"--booking-color-page: #000000;")

    assert response.status_code == 422
    assert "--booking-color-page" in response.text


async def test_fonts_upload_replace_and_remove(site: Site) -> None:
    await site.login_as(SUPER)

    upload = await _upload(site, "font/heading", "font", WOFF2)

    assert upload.status_code == 303
    assert upload.headers["location"] == f"{PAGE}?done=font_saved#fonts-heading"
    page = (await site.client.get(PAGE)).text
    assert "Koppen verwijderen" in page
    assert (await site.client.get("/o/demo/font/heading")).content == WOFF2
    assert '"OpenPlanner Heading"' in (await site.client.get("/o/demo/theme.css")).text

    removed = await site.client.post(f"{PAGE}/font/heading/remove", headers=SAME_ORIGIN)
    assert removed.headers["location"] == f"{PAGE}?done=font_removed#fonts-heading"
    assert (await site.client.get("/o/demo/font/heading")).status_code == 404


async def test_disguised_and_oversized_fonts_are_refused(site: Site) -> None:
    await site.login_as(SUPER)
    ttf = b"\x00\x01\x00\x00" + b"\x00" * 64

    refused = await _upload(site, "font/text", "font", ttf)
    huge = await _upload(site, "font/text", "font", WOFF2 + b"\x00" * 300_000)

    assert refused.status_code == 422
    assert 'href="#font-text"' in refused.text
    assert 'aria-describedby="font-text-hint font-text-error"' in refused.text
    assert huge.status_code == 413
    assert (await site.client.get("/o/demo/font/text")).status_code == 404
