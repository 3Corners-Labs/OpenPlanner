"""Changing and resetting platform settings (ADR 0042 build step 4)."""

from collections.abc import Mapping
from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Principal, Role
from app.domain.errors import DomainError, ErrorCode
from app.domain.settings_registry import BY_KEY
from app.domain.settings_service import CLI_ACTOR, SettingsService, editable, parse
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork

SUPER = Principal("sub-super", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
AUDITOR = Principal("sub-aud", "A", frozenset({Role.AUDITOR}), frozenset({"demo"}), frozenset())


def _service(
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
    *,
    locked: frozenset[str] = frozenset(),
    combination: str | None = None,
) -> SettingsService:
    def check(_: Mapping[str, object]) -> str | None:
        return combination

    return SettingsService(
        uow, clock, AdminService(uow, clock), locked=lambda: locked, combination=check
    )


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


# Which settings the portal may change in this step.


def test_operations_retention_storage_rates_and_sessions_are_editable() -> None:
    for key in (
        "worker_poll_seconds",
        "audit_retention_days",
        "storage_low_free_percent",
        "rate_limit_bookings",
        "admin_session_hours",
        "log_level",
    ):
        assert editable(BY_KEY[key]), key


def test_login_mapping_secrets_mail_and_environment_settings_are_not() -> None:
    for key in ("oidc_role_claim", "smtp_password", "smtp_host", "public_base_url", "time_format"):
        assert not editable(BY_KEY[key]), key


# Parsing what the form or the CLI sends.


@pytest.mark.parametrize(
    ("key", "raw", "value"),
    [
        ("admin_session_hours", " 4 ", 4),
        ("worker_poll_seconds", "7.5", 7.5),
        ("rate_limit_bookings", "20 per hour", "20/hour"),
        ("log_level", "debug", "DEBUG"),
        ("database_max_mb", "", None),
    ],
)
def test_parse_turns_text_into_the_registry_kind(key: str, raw: str, value: object) -> None:
    assert parse(BY_KEY[key], raw) == value


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("admin_session_hours", "9"),  # above the portal bound
        ("admin_session_hours", "four"),
        ("admin_session_hours", ""),  # not optional
        ("rate_limit_bookings", "31/hour"),
        ("rate_limit_bookings", "10/minute"),  # another period
        ("log_level", "TRACE"),
        ("database_max_mb", "99"),
    ],
)
def test_parse_refuses_what_the_portal_does_not_accept(key: str, raw: str) -> None:
    with pytest.raises(DomainError) as caught:
        parse(BY_KEY[key], raw)

    assert (caught.value.code, caught.value.field) == (ErrorCode.SETTING_INVALID, key)


# Changing.


async def test_a_change_is_stored_bumps_the_version_and_is_audited(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    await _service(uow, clock).change(SUPER, "admin_session_hours", "4")

    assert uow.state.platform_settings == {"admin_session_hours": 4}
    assert uow.state.settings_version == 1
    event = uow.state.audit[-1]
    assert (event.actor, event.action, event.outcome) == (
        "sub-super",
        "settings.platform.manage",
        Outcome.SUCCESS,
    )
    assert event.detail == "set:admin_session_hours:8->4"


async def test_the_auditor_may_not_change_a_setting(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    with pytest.raises(DomainError) as caught:
        await _service(uow, clock).change(AUDITOR, "admin_session_hours", "4")

    assert caught.value.code is ErrorCode.NOT_PERMITTED
    assert uow.state.platform_settings == {}


async def test_a_locked_setting_is_refused_and_the_refusal_audited(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    service = _service(uow, clock, locked=frozenset({"admin_session_hours"}))

    with pytest.raises(DomainError) as caught:
        await service.change(SUPER, "admin_session_hours", "4")

    assert caught.value.code is ErrorCode.SETTING_LOCKED
    assert uow.state.platform_settings == {}
    event = uow.state.audit[-1]
    assert (event.outcome, event.reason, event.detail) == (
        Outcome.DENIED,
        "setting_locked",
        "set:admin_session_hours",
    )


async def test_a_setting_outside_this_step_is_refused(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    with pytest.raises(DomainError) as caught:
        await _service(uow, clock).change(SUPER, "oidc_role_claim", "groups")

    assert caught.value.code is ErrorCode.SETTING_NOT_EDITABLE


async def test_values_that_break_a_rule_together_are_refused(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    service = _service(uow, clock, combination="STORAGE_CRITICAL_FREE_PERCENT must be below")

    with pytest.raises(DomainError) as caught:
        await service.change(SUPER, "storage_critical_free_percent", "20")

    assert caught.value.code is ErrorCode.SETTING_COMBINATION
    assert uow.state.platform_settings == {}


async def test_an_invalid_value_never_reaches_the_store_or_the_audit_detail(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    with pytest.raises(DomainError):
        await _service(uow, clock).change(SUPER, "admin_session_hours", "<script>")

    assert uow.state.platform_settings == {}
    assert "<script>" not in uow.state.audit[-1].detail


# The log level always expires (ADR 0042 §6).


async def test_the_log_level_needs_an_expiry_of_at_most_24_hours(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    service = _service(uow, clock)

    for hours in (None, 0, 25):
        with pytest.raises(DomainError) as caught:
            await service.change(SUPER, "log_level", "DEBUG", hours=hours)
        assert caught.value.code is ErrorCode.SETTING_EXPIRY

    await service.change(SUPER, "log_level", "DEBUG", hours=2)

    assert uow.state.platform_settings == {"log_level": "DEBUG"}
    assert uow.state.platform_expiries == {"log_level": clock.now() + timedelta(hours=2)}


async def test_other_settings_take_no_expiry(uow: InMemoryUnitOfWork, clock: FixedClock) -> None:
    with pytest.raises(DomainError) as caught:
        await _service(uow, clock).change(SUPER, "admin_session_hours", "4", hours=1)

    assert caught.value.code is ErrorCode.SETTING_EXPIRY


# Resetting, and the host CLI.


async def test_reset_removes_the_portal_value(uow: InMemoryUnitOfWork, clock: FixedClock) -> None:
    service = _service(uow, clock)
    await service.change(SUPER, "admin_session_hours", "4")

    assert await service.reset(SUPER, "admin_session_hours")

    assert uow.state.platform_settings == {}
    assert uow.state.settings_version == 2
    assert uow.state.audit[-1].detail == "reset:admin_session_hours:4"


async def test_resetting_what_is_not_set_changes_nothing(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    assert not await _service(uow, clock).reset(SUPER, "admin_session_hours")

    assert uow.state.settings_version == 0


async def test_the_cli_changes_and_resets_as_the_host_actor(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    service = _service(uow, clock)
    await service.change_host("admin_session_hours", "4")
    await service.change_host("audit_retention_days", "400")

    assert await service.reset_all_host() == 2

    assert uow.state.platform_settings == {}
    assert {e.actor for e in uow.state.audit} == {CLI_ACTOR}
