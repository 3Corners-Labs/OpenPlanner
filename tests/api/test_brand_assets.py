"""Branding on citizen pages (WP3b): theme stylesheet, logo and the page header."""

from collections.abc import AsyncIterator

import httpx
import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.api.brand_assets import theme_css
from app.config import Settings
from app.domain.branding import Branding, Logo
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW

PNG = b"\x89PNG\r\n\x1a\n" + b"\x07" * 64


async def _brand(database_url: str, branding: Branding, logo: Logo | None = None) -> None:
    engine = create_engine(database_url)
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        demo = await tx.get_organisation("demo")
        assert demo is not None
        await tx.save_branding(demo.id, branding)
        if logo is not None:
            await tx.save_logo(demo.id, logo)
    await engine.dispose()


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        yield client


def test_theme_contains_only_the_validated_colour() -> None:
    assert theme_css(Branding(accent_color="#154273")) == (
        ":root {\n  --booking-color-accent: #154273;\n}\n"
    )


async def test_default_branding_uses_the_organisation_name(site: httpx.AsyncClient) -> None:
    page = (await site.get("/g/demo")).text
    theme = await site.get("/o/demo/theme.css")

    assert "<span>Demo-gemeente</span>" in page
    assert 'href="/o/demo/theme.css?v=01689B"' in page
    assert "site-logo" not in page
    assert theme.status_code == 200
    assert theme.headers["content-type"].startswith("text/css")
    assert theme.headers["cache-control"] == "public, max-age=3600"
    assert theme.headers["x-content-type-options"] == "nosniff"
    assert "#01689B" in theme.text


async def test_branding_reaches_every_citizen_page(
    site: httpx.AsyncClient, demo_settings: Settings
) -> None:
    logo = Logo.from_upload(PNG)
    await _brand(demo_settings.database_url, Branding("Gemeente Demo", "#154273"), logo)

    group = (await site.get("/g/demo")).text
    direct = (await site.get("/g/demo/direct")).text
    image = await site.get("/o/demo/logo")

    for page in (group, direct):
        assert "<span>Gemeente Demo</span>" in page
        assert 'href="/o/demo/theme.css?v=154273"' in page
        assert f'<img class="site-logo" src="/o/demo/logo?v={logo.digest}" alt="">' in page
    assert image.content == PNG
    assert image.headers["content-type"] == "image/png"
    assert "#154273" in (await site.get("/o/demo/theme.css")).text


async def test_unknown_organisation_and_missing_logo(site: httpx.AsyncClient) -> None:
    assert (await site.get("/o/nope/theme.css")).status_code == 404
    assert (await site.get("/o/nope/logo")).status_code == 404
    assert (await site.get("/o/demo/logo")).status_code == 404
