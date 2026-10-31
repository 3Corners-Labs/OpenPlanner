"""Branding on participant pages (WP3b, ADR 0041): theme stylesheet, logo, fonts and header."""

from collections.abc import AsyncIterator

import httpx
import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.branding import Branding, FontFile, FontRef, FontRole, HeaderStyle, Logo
from app.domain.scheduling import Organisation
from app.domain.theme import Theme
from app.web.branding import theme_css
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW

PNG = b"\x89PNG\r\n\x1a\n" + b"\x07" * 64
WOFF2 = b"wOF2" + b"\x07" * 64


async def _brand(
    database_url: str,
    branding: Branding,
    logo: Logo | None = None,
    font: FontFile | None = None,
) -> None:
    engine = create_engine(database_url)
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        demo = await tx.get_organisation("demo")
        assert demo is not None
        await tx.save_branding(demo.id, branding)
        if logo is not None:
            await tx.save_logo(demo.id, logo)
        if font is not None:
            await tx.save_font(demo.id, font.role, font)
    await engine.dispose()


def _css(branding: Branding) -> str:
    return theme_css(Organisation(1, "Demo", "demo", branding=branding))


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        yield client


def test_theme_contains_only_the_validated_colour() -> None:
    assert _css(Branding(accent_color="#154273")) == (
        ":root {\n  --booking-color-accent: #154273;\n}\n"
    )


def test_theme_colours_give_way_to_prefers_contrast_more() -> None:
    theme = Theme.of({"color-text": "#0B0C0C", "radius": "0px", "shadow": "none"})

    css = _css(Branding(theme=theme))

    assert css == (
        ":root {\n"
        "  --booking-color-accent: #01689B;\n"
        "  --booking-radius: 0px;\n"
        "  --booking-shadow: none;\n"
        "}\n"
        "@media not (prefers-contrast: more) {\n"
        "  :root {\n"
        "    --booking-color-text: #0B0C0C;\n"
        "  }\n"
        "}\n"
    )


def test_uploaded_fonts_are_declared_from_our_origin_and_put_first() -> None:
    text, heading = FontRef(FontRole.TEXT, "t" * 16), FontRef(FontRole.HEADING, "h" * 16)
    theme = Theme.of({"font-family": "Georgia, serif"})

    css = _css(Branding(theme=theme, fonts=(text, heading)))

    assert '  src: url("/o/demo/font/text?v=tttttttttttttttt") format("woff2");\n' in css
    assert '  src: url("/o/demo/font/heading?v=hhhhhhhhhhhhhhhh") format("woff2");\n' in css
    assert "  font-display: swap;\n" in css
    assert '  --booking-font-family: "OpenPlanner Text", Georgia, serif;\n' in css
    assert (
        '  --booking-font-family-heading: "OpenPlanner Heading", var(--booking-font-family);\n'
        in css
    )
    assert "http" not in css


def test_font_stacks_without_uploads_are_passed_on() -> None:
    theme = Theme.of({"font-family-heading": '"Fira Sans", sans-serif'})
    css = _css(Branding(theme=theme))
    assert '  --booking-font-family-heading: "Fira Sans", sans-serif;\n' in css
    assert "@font-face" not in css


async def test_default_branding_uses_the_organisation_name(site: httpx.AsyncClient) -> None:
    page = (await site.get("/g/demo")).text
    theme = await site.get("/o/demo/theme.css")

    assert "<span>Demo-gemeente</span>" in page
    assert f'href="/o/demo/theme.css?v={Branding().theme_digest}"' in page
    assert '<header class="site-header site-header--standard">' in page
    assert "site-logo" not in page
    assert theme.status_code == 200
    assert theme.headers["content-type"].startswith("text/css")
    assert theme.headers["cache-control"] == "public, max-age=3600"
    assert theme.headers["x-content-type-options"] == "nosniff"
    assert "#01689B" in theme.text


async def test_branding_reaches_every_participant_page(
    site: httpx.AsyncClient, demo_settings: Settings
) -> None:
    logo = Logo.from_upload(PNG)
    branding = Branding("Gemeente Demo", "#154273")
    await _brand(demo_settings.database_url, branding, logo)

    group = (await site.get("/g/demo")).text
    direct = (await site.get("/g/demo/direct")).text
    image = await site.get("/o/demo/logo")

    for page in (group, direct):
        assert "<span>Gemeente Demo</span>" in page
        assert f'href="/o/demo/theme.css?v={branding.theme_digest}"' in page
        assert f'<img class="site-logo" src="/o/demo/logo?v={logo.digest}" alt="">' in page
    assert image.content == PNG
    assert image.headers["content-type"] == "image/png"
    assert "#154273" in (await site.get("/o/demo/theme.css")).text


async def test_unknown_organisation_and_missing_logo(site: httpx.AsyncClient) -> None:
    assert (await site.get("/o/nope/theme.css")).status_code == 404
    assert (await site.get("/o/nope/logo")).status_code == 404
    assert (await site.get("/o/demo/logo")).status_code == 404


async def test_fonts_are_served_as_woff2_and_404_otherwise(
    site: httpx.AsyncClient, demo_settings: Settings
) -> None:
    font = FontFile.from_upload(FontRole.TEXT, WOFF2)
    await _brand(demo_settings.database_url, Branding(), font=font)

    served = await site.get("/o/demo/font/text")
    theme = (await site.get("/o/demo/theme.css")).text

    assert served.content == WOFF2
    assert served.headers["content-type"] == "font/woff2"
    assert served.headers["cache-control"] == "public, max-age=3600"
    assert f"/o/demo/font/text?v={font.digest}" in theme
    assert (await site.get("/o/demo/font/heading")).status_code == 404
    assert (await site.get("/o/demo/font/../logo")).status_code == 404
    assert (await site.get("/o/demo/font/bogus")).status_code == 404
    assert (await site.get("/o/nope/font/text")).status_code == 404


async def test_participant_pages_allow_fonts_from_our_origin_only(site: httpx.AsyncClient) -> None:
    csp = (await site.get("/g/demo")).headers["content-security-policy"]
    assert "font-src 'self';" in csp


async def test_accent_header_and_logo_only_header(
    site: httpx.AsyncClient, demo_settings: Settings
) -> None:
    await _brand(demo_settings.database_url, Branding(header_style=HeaderStyle.ACCENT))
    assert '<header class="site-header site-header--accent">' in (await site.get("/g/demo")).text

    logo = Logo.from_upload(PNG)
    await _brand(
        demo_settings.database_url, Branding("Gemeente Demo", header_style=HeaderStyle.LOGO), logo
    )
    page = (await site.get("/g/demo")).text
    assert '<header class="site-header site-header--logo">' in page
    assert f'<img class="site-logo" src="/o/demo/logo?v={logo.digest}" alt="Gemeente Demo">' in page
    assert "<span>Gemeente Demo</span>" not in page  # the name is in the alt text, once
