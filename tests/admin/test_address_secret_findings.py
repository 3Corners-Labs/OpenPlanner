"""System status says when a previous webhook secret is still set (ADR 0124 point 8)."""

from datetime import timedelta
from typing import Any

from pydantic import SecretStr

from tests.admin.conftest import AUDITOR, Site


def _settings(site: Site, **update: Any) -> None:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    app.state.settings = app.state.settings.model_copy(update=update)


async def _part(site: Site) -> str:
    text = (await site.client.get("/admin/settings")).text
    return text.split('id="address-heading"')[1].split('id="tls-heading"')[0]


async def test_a_previous_value_inside_its_overlap_is_no_finding_and_after_it_is_one(
    site: Site,
) -> None:
    _settings(
        site,
        wxcc_webhook_secret=SecretStr("current-secret-value"),
        wxcc_webhook_secret_previous=SecretStr("previous-secret-value"),
    )
    await site.login_as(AUDITOR)

    inside = await _part(site)
    site.clock.advance(timedelta(minutes=61))
    await site.login_as(AUDITOR)  # the idle session ended
    after = await _part(site)

    assert "Er staat nog een vorig webhookgeheim" not in inside
    assert "Er staat nog een vorig webhookgeheim in de omgeving" in after
    assert "WXCC_WEBHOOK_SECRET_PREVIOUS" in after  # says what to remove
    assert "Contactcenter (standaard)" in after  # and for which platform
    for text in (inside, after):
        assert "previous-secret-value" not in text
        assert "current-secret-value" not in text


async def test_a_previous_value_equal_to_the_current_one_is_a_finding_at_once(site: Site) -> None:
    _settings(
        site,
        webex_webhook_secret=SecretStr("same-secret-value"),
        webex_webhook_secret_previous=SecretStr("same-secret-value"),
    )
    await site.login_as(AUDITOR)

    part = await _part(site)

    assert "gelijk is aan het huidige" in part
    assert "Videoplatform" in part
    assert "same-secret-value" not in part


async def test_without_a_previous_value_there_is_no_finding(site: Site) -> None:
    _settings(site, webex_webhook_secret=SecretStr("only-secret-value"))
    await site.login_as(AUDITOR)

    assert "vorig webhookgeheim" not in await _part(site)
