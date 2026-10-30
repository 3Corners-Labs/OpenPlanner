"""Live settings: portal values applied on top of the environment, reloaded on change (§6)."""

import asyncio
from datetime import timedelta

from app.adapters.live_settings import LiveSettings, keep_current
from app.config import Settings
from app.domain.platform_settings import PlatformSettingsStore
from app.domain.scheduling import Organisation
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork


def _live(uow: InMemoryUnitOfWork, applied: list[Settings]) -> LiveSettings:
    store = PlatformSettingsStore(uow)
    return LiveSettings(Settings(environment="dev"), {}, store, FixedClock(), applied.append)


async def test_a_change_is_applied_and_handed_to_the_callback() -> None:
    uow, applied = InMemoryUnitOfWork(), []
    live = _live(uow, applied)
    await live.refresh()
    uow.state.settings_version = 1
    uow.state.platform_settings = {"admin_session_hours": 3}

    changed = await live.refresh()

    assert changed
    assert live.settings.admin_session_hours == 3
    assert applied[-1] is live.settings


async def test_without_a_change_the_callback_is_not_called_again() -> None:
    uow, applied = InMemoryUnitOfWork(), []
    live = _live(uow, applied)
    await live.refresh()
    calls = len(applied)

    assert not await live.refresh()
    assert len(applied) == calls


async def test_the_states_show_where_each_value_comes_from() -> None:
    uow = InMemoryUnitOfWork()
    uow.state.settings_version = 1
    uow.state.platform_settings = {"admin_session_hours": 3}
    live = _live(uow, [])
    await live.refresh()

    state = next(s for s in live.states() if s.spec.key == "admin_session_hours")

    assert (state.source, state.shown) == ("portal", "3")


async def test_a_failed_read_keeps_the_last_values() -> None:
    class Broken(InMemoryUnitOfWork):
        def read(self):  # type: ignore[no-untyped-def, override]
            raise RuntimeError("no such table")

    applied: list[Settings] = []
    live = _live(Broken(), applied)

    assert not await live.refresh()
    assert live.settings.admin_session_hours == 8
    assert applied == []


async def test_keep_current_refreshes_until_cancelled() -> None:
    uow, applied = InMemoryUnitOfWork(), []
    live = _live(uow, applied)
    await live.refresh()
    uow.state.settings_version = 1
    uow.state.platform_settings = {"admin_session_hours": 2}

    task = asyncio.create_task(keep_current(live, interval=0.001))
    for _ in range(100):
        await asyncio.sleep(0.001)
        if live.settings.admin_session_hours == 2:
            break
    task.cancel()

    assert live.settings.admin_session_hours == 2


async def test_values_that_break_a_rule_together_are_refused_together() -> None:
    uow = InMemoryUnitOfWork()
    uow.state.settings_version = 1
    # Each within its bounds, but critical is not below low (base: low 15, critical 5).
    uow.state.platform_settings = {"storage_critical_free_percent": 20.0}
    live = _live(uow, [])

    await live.refresh()

    assert live.refused
    assert live.settings.storage_critical_free_percent == 5.0


async def test_a_log_level_falls_back_once_its_expiry_passes() -> None:
    uow, clock, applied = InMemoryUnitOfWork(), FixedClock(), []
    uow.state.settings_version = 1
    uow.state.platform_settings = {"log_level": "DEBUG"}
    uow.state.platform_expiries = {"log_level": clock.now() + timedelta(hours=1)}
    live = LiveSettings(
        Settings(environment="dev"), {}, PlatformSettingsStore(uow), clock, applied.append
    )
    await live.refresh()
    assert live.settings.log_level == "DEBUG"

    clock.advance(timedelta(hours=1))

    assert await live.refresh()  # no new version, but the value lapsed
    assert live.settings.log_level == "INFO"
    assert applied[-1].log_level == "INFO"


async def test_locked_keys_and_combinations_follow_the_environment() -> None:
    live = LiveSettings(
        Settings(environment="dev"),
        {"ADMIN_SESSION_HOURS": "6"},
        PlatformSettingsStore(InMemoryUnitOfWork()),
        FixedClock(),
    )

    assert "admin_session_hours" in live.locked()
    assert live.combination_problem({"storage_critical_free_percent": 20.0}) is not None
    assert live.combination_problem({"storage_critical_free_percent": 2.0}) is None


# Organisation values (ADR 0042 step 5).


async def _with_organisations(values: dict[int, dict[str, object]]) -> LiveSettings:
    uow = InMemoryUnitOfWork()
    uow.state.organisations[7] = Organisation(7, "Demo", "demo")
    uow.state.organisations[8] = Organisation(8, "Andere", "andere")
    uow.state.settings_version = 1
    uow.state.organisation_settings = values
    live = _live(uow, [])
    await live.refresh()
    return live


async def test_an_organisation_gets_its_own_values_over_the_platform() -> None:
    live = await _with_organisations({7: {"time_format": "12h", "shortfall_horizon_days": 30}})

    own = live.for_organisation(live.organisation_id("demo"))
    other = live.for_organisation(live.organisation_id("andere"))

    assert (own.time_format, own.shortfall_horizon_days) == ("12h", 30)
    assert (other.time_format, other.shortfall_horizon_days) == ("24h", 14)
    assert live.for_organisation(None) is live.settings


async def test_organisation_values_that_break_a_rule_together_do_not_apply() -> None:
    live = await _with_organisations({7: {"planner_rebook_reminder_days": 20}})  # lapse 14

    assert live.for_organisation(7).planner_rebook_reminder_days == 3
    assert live.organisation_combination_problem(7, {"planner_rebook_reminder_days": 20})
    assert live.organisation_combination_problem(7, {"planner_rebook_reminder_days": 2}) is None


async def test_the_organisation_states_show_the_source() -> None:
    live = await _with_organisations({7: {"time_format": "12h"}})

    states = {s.spec.key: s for s in live.organisation_states(7)}

    assert set(states) == {
        "time_format",
        "admin_timezone",
        "product_footer",
        "staff_dormant_after_days",
        "shortfall_horizon_days",
        "planner_rebook_days",
        "planner_rebook_reminder_days",
        "reschedule_proposal_confirm_hours",
        "reschedule_proposal_reminder_hours",
        "phone_default_region",
        "booking_confirm_window_hours",
        "booking_confirm_cutoff_hours",
        "booking_confirm_reminders",
    }
    assert (states["time_format"].source, states["time_format"].shown) == ("organisation", "12h")
    assert states["admin_timezone"].source == "default"
