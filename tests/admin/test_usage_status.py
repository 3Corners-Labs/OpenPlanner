"""The *Usage reporting* part of System status (ADR 0117 point 4)."""

from datetime import timedelta

from app.domain.usage import FactKind, UsageFact, hour_of
from tests.support.admin_site import AUDITOR, SUPER, Site, site_uow


async def _fact(site: Site) -> None:
    async with site_uow(site).locked() as tx:
        await tx.record_usage(UsageFact(FactKind.BOOKING_SLOTTED, hour_of(site.clock.now()), 1, 1))


async def _roll(site: Site) -> None:
    async with site_uow(site).locked() as tx:
        await tx.advance_usage_watermark(1, site.clock.now())


async def _part(site: Site, lang: str = "nl") -> str:
    text = (await site.client.get(f"/admin/settings?lang={lang}")).text
    return text.split('id="usage-heading"')[1].split('id="address-heading"')[0]


async def test_without_facts_nothing_waits_and_nothing_warns(site: Site) -> None:
    await site.login_as(AUDITOR)

    part = await _part(site)

    assert "Wachtende feiten" in part
    assert "Nog niet" in part
    assert "chip--warning" not in part


async def test_waiting_facts_that_the_worker_has_not_touched_warn(site: Site) -> None:
    await _fact(site)
    await site.login_as(SUPER)

    part = await _part(site)

    assert 'role="alert"' in part
    assert "60 minuten" in part


async def test_a_recent_run_with_nothing_waiting_does_not_warn(site: Site) -> None:
    await _roll(site)
    await site.login_as(SUPER)

    part = await _part(site)

    assert "chip--warning" not in part


async def test_a_run_long_ago_with_facts_waiting_warns(site: Site) -> None:
    await _fact(site)
    await _roll(site)
    await _fact(site)
    site.clock.advance(timedelta(hours=2))
    await site.login_as(SUPER)

    assert 'role="alert"' in await _part(site)


async def test_the_english_page_uses_english_words(site: Site) -> None:
    await _fact(site)
    await site.login_as(SUPER)

    part = await _part(site, "en")

    assert "Facts waiting" in part
    assert "behind or not running" in part


async def test_an_unreadable_database_is_a_warning_not_a_healthy_card(site: Site) -> None:
    app = site.app
    async with app.state.engine.begin() as connection:
        await connection.exec_driver_sql("DROP TABLE usage_events")
    await site.login_as(SUPER)

    part = await _part(site)

    assert 'role="alert"' in part
    assert "konden niet" in part
