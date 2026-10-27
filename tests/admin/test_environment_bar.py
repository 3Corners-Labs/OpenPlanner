"""The environment bar on admin pages (ENVIRONMENT=test in the test suite)."""

from tests.admin.conftest import ORG_ADMIN, Site


async def test_every_admin_page_shows_the_bar_in_test(site: Site) -> None:
    logged_out = (await site.client.get("/admin/logged-out")).text
    await site.login_as(ORG_ADMIN)
    overview = (await site.client.get("/admin/")).text

    for page in (logged_out, overview):
        assert '<div class="env-bar" role="note" aria-label="Omgeving">' in page
        assert "<strong>Testomgeving</strong>" in page
        assert "Simulator staat aan" not in page  # off by default
