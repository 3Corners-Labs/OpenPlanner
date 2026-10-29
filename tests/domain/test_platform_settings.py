"""Portal values, the environment lock and reloading by ``settings_version`` (ADR 0042 §2, §6)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.platform_settings import (
    EMPTY,
    RELOAD_SECONDS,
    PlatformSettingsStore,
    PortalValues,
    Source,
    resolve,
    resolve_for_organisation,
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


def test_an_expired_portal_value_no_longer_applies() -> None:
    now = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
    portal = PortalValues(1, {"log_level": "DEBUG"}, {"log_level": now + timedelta(hours=1)})

    before = resolve(LEVEL, "INFO", None, portal, now)
    after = resolve(LEVEL, "INFO", None, portal, now + timedelta(hours=1))

    assert (before.value, before.source) == ("DEBUG", Source.PORTAL)
    assert (after.value, after.source, after.portal_ignored) == ("INFO", Source.DEFAULT, False)
    assert portal.next_expiry(now) == now + timedelta(hours=1)
    assert portal.next_expiry(now + timedelta(hours=1)) is None


async def test_the_store_reads_the_expiries_too() -> None:
    uow = InMemoryUnitOfWork()
    at = datetime(2026, 10, 29, 13, 0, tzinfo=UTC)
    uow.state.platform_settings = {"log_level": "DEBUG"}
    uow.state.platform_expiries = {"log_level": at}
    store = PlatformSettingsStore(uow)

    await store.refresh()

    assert store.current.expires == {"log_level": at}


ZONE = BY_KEY["admin_timezone"]  # class D string: must be an IANA time zone
CLOCK = BY_KEY["time_format"]  # class D enum


def test_a_time_zone_must_be_a_real_iana_zone() -> None:
    assert usable(ZONE, "Europe/Amsterdam")
    assert usable(ZONE, "America/Curacao")
    assert not usable(ZONE, "Mars/Olympus")
    assert not usable(ZONE, "../../etc/passwd")


def test_an_organisation_value_overrides_the_platform_value() -> None:
    portal = PortalValues(1, {"time_format": "12h"}, organisations={7: {"time_format": "locale"}})

    own = resolve_for_organisation(CLOCK, "12h", Source.PORTAL, portal, 7)
    other = resolve_for_organisation(CLOCK, "12h", Source.PORTAL, portal, 8)

    assert (own.value, own.source) == ("locale", Source.ORGANISATION)
    assert (other.value, other.source) == ("12h", Source.PORTAL)


def test_the_environment_lock_does_not_bind_an_organisation() -> None:
    # ADR 0042 §2: env locks apply to the platform default, not to individual organisations.
    portal = PortalValues(1, organisations={7: {"time_format": "locale"}})

    resolved = resolve_for_organisation(CLOCK, "24h", Source.ENV, portal, 7)

    assert (resolved.value, resolved.source, resolved.locked) == (
        "locale",
        Source.ORGANISATION,
        False,
    )


def test_an_invalid_organisation_value_falls_back_to_the_platform() -> None:
    portal = PortalValues(1, organisations={7: {"admin_timezone": "Mars/Olympus"}})

    resolved = resolve_for_organisation(ZONE, "Europe/Amsterdam", Source.DEFAULT, portal, 7)

    assert (resolved.value, resolved.source, resolved.portal_ignored) == (
        "Europe/Amsterdam",
        Source.DEFAULT,
        True,
    )


async def test_the_store_reads_the_organisation_values() -> None:
    uow = InMemoryUnitOfWork()
    uow.state.organisation_settings = {7: {"time_format": "12h"}}
    store = PlatformSettingsStore(uow)

    await store.refresh()

    assert store.current.organisations == {7: {"time_format": "12h"}}
