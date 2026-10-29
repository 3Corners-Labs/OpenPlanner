"""Live settings: portal values applied on top of the environment, reloaded on change (§6)."""

import asyncio

from app.adapters.live_settings import LiveSettings, keep_current
from app.config import Settings
from app.domain.platform_settings import PlatformSettingsStore
from tests.fakes.persistence import InMemoryUnitOfWork


def _live(uow: InMemoryUnitOfWork, applied: list[Settings]) -> LiveSettings:
    return LiveSettings(Settings(environment="dev"), {}, PlatformSettingsStore(uow), applied.append)


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
