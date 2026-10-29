"""Portal values, the environment lock and reloading by ``settings_version`` (ADR 0042 §2, §6)."""

import pytest

from app.domain.platform_settings import (
    EMPTY,
    RELOAD_SECONDS,
    PlatformSettingsStore,
    PortalValues,
    Source,
    resolve,
    usable,
)
from app.domain.settings_registry import BY_KEY
from tests.fakes.persistence import InMemoryUnitOfWork

HOURS = BY_KEY["admin_session_hours"]  # class C int, portal bounds 1-8, default 8
LEVEL = BY_KEY["log_level"]  # class C enum
RATE = BY_KEY["rate_limit_bookings"]  # class C rate, 1-30 per hour
SECRET = BY_KEY["smtp_password"]  # class C secret: never a portal value in this table
MAX_MB = BY_KEY["database_max_mb"]  # class C optional int
PUBLIC_URL = BY_KEY["public_base_url"]  # class B


def test_the_default_applies_without_a_portal_value_or_an_environment_variable() -> None:
    resolved = resolve(HOURS, 8, None, EMPTY)

    assert (resolved.value, resolved.source, resolved.locked) == (8, Source.DEFAULT, False)


def test_a_portal_value_overrides_the_default() -> None:
    resolved = resolve(HOURS, 8, None, PortalValues(3, {"admin_session_hours": 4}))

    assert (resolved.value, resolved.source, resolved.locked) == (4, Source.PORTAL, False)


def test_the_environment_locks_the_setting_and_wins_over_the_portal() -> None:
    resolved = resolve(HOURS, 6, Source.ENV, PortalValues(3, {"admin_session_hours": 4}))

    assert (resolved.value, resolved.source, resolved.locked) == (6, Source.ENV, True)


def test_an_environment_value_outside_the_portal_bounds_is_kept_but_flagged() -> None:
    resolved = resolve(HOURS, 12, Source.ENV, EMPTY)

    assert resolved.value == 12
    assert resolved.outside_bounds


def test_a_deployment_setting_is_never_locked_nor_taken_from_the_portal() -> None:
    portal = PortalValues(1, {"public_base_url": "https://evil.example"})

    from_env = resolve(PUBLIC_URL, "https://a.example", Source.ENV, portal)
    default = resolve(PUBLIC_URL, "http://localhost:8000", None, portal)

    assert (from_env.source, from_env.locked) == (Source.ENV, False)
    assert (default.value, default.source) == ("http://localhost:8000", Source.DEFAULT)
    assert not default.portal_ignored


@pytest.mark.parametrize(
    ("spec", "value"),
    [
        (HOURS, 9),  # above the portal bound
        (HOURS, 0),  # below it
        (HOURS, "4"),  # a string where a number belongs
        (HOURS, True),  # a bool is not an int here
        (HOURS, None),  # not optional
        (LEVEL, "TRACE"),  # not one of the choices
        (RATE, "10/minute"),  # another period than the default's
        (RATE, "31/hour"),
        (SECRET, "hunter2"),  # secrets live sealed in integration_credentials (step 6)
    ],
)
def test_a_stored_value_that_breaks_the_rules_is_ignored(spec: object, value: object) -> None:
    portal = PortalValues(2, {spec.key: value})  # type: ignore[attr-defined]

    resolved = resolve(spec, spec.default, None, portal)  # type: ignore[arg-type, attr-defined]

    assert resolved.source is Source.DEFAULT
    assert resolved.portal_ignored
    assert not usable(spec, value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("spec", "value"),
    [(HOURS, 1), (HOURS, 8), (LEVEL, "DEBUG"), (RATE, "30/hour"), (MAX_MB, None), (MAX_MB, 100)],
)
def test_a_value_within_the_rules_is_usable(spec: object, value: object) -> None:
    assert usable(spec, value)  # type: ignore[arg-type]


def test_a_float_setting_accepts_a_whole_number() -> None:
    assert usable(BY_KEY["worker_poll_seconds"], 30)


async def test_the_store_loads_on_the_first_refresh() -> None:
    uow = InMemoryUnitOfWork()
    uow.state.settings_version = 4
    uow.state.platform_settings = {"admin_session_hours": 4}
    store = PlatformSettingsStore(uow)

    assert await store.refresh()
    assert store.current == PortalValues(4, {"admin_session_hours": 4})


async def test_a_new_version_reloads_the_values() -> None:
    uow = InMemoryUnitOfWork()
    store = PlatformSettingsStore(uow)
    await store.refresh()
    uow.state.settings_version = 1
    uow.state.platform_settings = {"admin_session_hours": 2}

    assert await store.refresh()
    assert store.current == PortalValues(1, {"admin_session_hours": 2})


async def test_an_unchanged_version_reads_no_values() -> None:
    uow = InMemoryUnitOfWork()
    store = PlatformSettingsStore(uow)
    await store.refresh()
    uow.state.platform_settings = {"admin_session_hours": 2}  # written without a new version

    assert not await store.refresh()
    assert store.current == EMPTY


def test_the_reload_interval_is_the_adr_bound() -> None:
    assert RELOAD_SECONDS == 10
