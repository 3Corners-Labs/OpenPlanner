"""Changing and resetting platform settings (ADR 0042 build step 4)."""

from collections.abc import Mapping
from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Role
from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import Organisation
from app.domain.settings_registry import BY_KEY
from app.domain.settings_service import CLI_ACTOR, SettingsService, editable, parse
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.principals import principal

SUPER = principal("sub-super", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
AUDITOR = principal("sub-aud", "A", frozenset({Role.AUDITOR}), frozenset({"demo"}), frozenset())


def _service(
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
    *,
    locked: frozenset[str] = frozenset(),
    combination: str | None = None,
    environment: dict[str, object] | None = None,
) -> SettingsService:
    def check(_: Mapping[str, object]) -> str | None:
        return combination

    return SettingsService(
        uow,
        clock,
        AdminService(uow, clock),
        locked=lambda: locked,
        combination=check,
        environment_value=(environment or {}).get,
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
    for key in ("oidc_role_claim", "webex_webhook_secret", "public_base_url"):
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


# Organisation values (ADR 0042 step 5).

ORG = principal("sub-org", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset())


def _organisations(uow: InMemoryUnitOfWork) -> None:
    uow.state.organisations[7] = Organisation(7, "Demo", "demo")
    uow.state.organisations[8] = Organisation(8, "Andere", "andere")


async def test_an_org_admin_sets_a_value_for_their_own_organisation(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    _organisations(uow)

    await _service(uow, clock).change_organisation(ORG, "demo", "time_format", "12h")

    assert uow.state.organisation_settings == {7: {"time_format": "12h"}}
    assert uow.state.settings_version == 1
    event = uow.state.audit[-1]
    assert (event.actor, event.action, event.organisation, event.detail) == (
        "sub-org",
        "settings.organisation.manage",
        "demo",
        "set:time_format:24h->12h",
    )


async def test_an_org_admin_may_not_set_another_organisation(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    _organisations(uow)

    with pytest.raises(DomainError) as caught:
        await _service(uow, clock).change_organisation(ORG, "andere", "time_format", "12h")

    assert caught.value.code is ErrorCode.NOT_PERMITTED
    assert uow.state.organisation_settings == {}


async def test_only_class_d_settings_are_set_per_organisation(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    _organisations(uow)

    with pytest.raises(DomainError) as caught:
        await _service(uow, clock).change_organisation(ORG, "demo", "admin_session_hours", "4")

    assert caught.value.code is ErrorCode.SETTING_NOT_EDITABLE


async def test_an_unknown_time_zone_is_refused(uow: InMemoryUnitOfWork, clock: FixedClock) -> None:
    _organisations(uow)

    with pytest.raises(DomainError) as caught:
        await _service(uow, clock).change_organisation(
            ORG, "demo", "admin_timezone", "Mars/Olympus"
        )

    assert caught.value.code is ErrorCode.SETTING_INVALID


async def test_the_environment_lock_does_not_stop_an_organisation(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    _organisations(uow)
    service = _service(uow, clock, locked=frozenset({"time_format"}))

    await service.change_organisation(ORG, "demo", "time_format", "12h")

    assert uow.state.organisation_settings == {7: {"time_format": "12h"}}


async def test_rebook_periods_are_checked_together_per_organisation(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    _organisations(uow)
    service = _service(uow, clock, combination="PLANNER_REBOOK_REMINDER_DAYS must be below")

    with pytest.raises(DomainError) as caught:
        await service.change_organisation(ORG, "demo", "planner_rebook_reminder_days", "20")

    assert caught.value.code is ErrorCode.SETTING_COMBINATION


async def test_reset_returns_the_organisation_to_the_platform_value(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    _organisations(uow)
    service = _service(uow, clock)
    await service.change_organisation(ORG, "demo", "shortfall_horizon_days", "30")

    assert await service.reset_organisation(ORG, "demo", "shortfall_horizon_days")
    assert not await service.reset_organisation(ORG, "demo", "shortfall_horizon_days")

    assert uow.state.organisation_settings == {7: {}}
    assert uow.state.audit[-1].detail == "reset:shortfall_horizon_days:30"


async def test_an_unknown_organisation_is_not_found(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    super_service = _service(uow, clock)

    with pytest.raises(DomainError) as caught:
        await super_service.change_organisation(SUPER, "nope", "time_format", "12h")

    assert caught.value.code is ErrorCode.ORGANISATION_NOT_FOUND


def test_the_platform_default_of_a_class_d_setting_is_editable() -> None:
    assert editable(BY_KEY["time_format"])
    assert editable(BY_KEY["planner_rebook_days"])


async def test_an_org_admin_lists_and_opens_only_their_own_organisations(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    _organisations(uow)
    service = _service(uow, clock)

    listed = await service.organisations(ORG)
    opened = await service.organisation(ORG, "demo")

    assert [o.slug for o in listed] == ["demo"]
    assert opened.id == 7
    with pytest.raises(DomainError) as caught:
        await service.organisation(ORG, "andere")
    assert caught.value.code is ErrorCode.NOT_PERMITTED


# Secrets and taking a value over from the environment (ADR 0042 step 6, §12).


async def test_a_secret_is_sealed_and_audited_without_its_value(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    await _service(uow, clock).change(SUPER, "smtp_password", "s3cret")

    assert uow.state.platform_secrets == {"smtp_password": "s3cret"}
    assert uow.state.platform_settings == {}
    assert uow.state.settings_version == 1
    assert uow.state.audit[-1].detail == "set:smtp_password:secret"
    assert all("s3cret" not in e.detail for e in uow.state.audit)


async def test_an_empty_secret_is_refused_reset_clears_it(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    service = _service(uow, clock)
    with pytest.raises(DomainError) as caught:
        await service.change(SUPER, "update_check_token", "  ")
    assert caught.value.code is ErrorCode.SETTING_INVALID

    await service.change(SUPER, "update_check_token", "ghp_x")
    assert await service.reset(SUPER, "update_check_token")

    assert uow.state.platform_secrets == {}
    assert uow.state.audit[-1].detail == "reset:update_check_token:secret"


async def test_without_a_settings_key_a_secret_is_refused(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    uow.state.secrets_available = False

    with pytest.raises(DomainError) as caught:
        await _service(uow, clock).change(SUPER, "smtp_password", "s3cret")

    assert caught.value.code is ErrorCode.SETTING_NO_KEY


async def test_the_mail_server_and_the_update_repository_are_editable() -> None:
    for key in ("smtp_host", "smtp_password", "mail_from", "update_repository"):
        assert editable(BY_KEY[key]), key


async def test_a_locked_value_is_taken_over_from_the_environment(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    service = _service(
        uow,
        clock,
        locked=frozenset({"smtp_host", "smtp_password"}),
        environment={"smtp_host": "mail.example.nl", "smtp_password": "from-env"},
    )

    await service.adopt(SUPER, "smtp_host")
    await service.adopt(SUPER, "smtp_password")

    assert uow.state.platform_settings == {"smtp_host": "mail.example.nl"}
    assert uow.state.platform_secrets == {"smtp_password": "from-env"}
    details = [e.detail for e in uow.state.audit[-2:]]
    assert details == ["adopt:smtp_host:mail.example.nl", "adopt:smtp_password:secret"]


async def test_only_a_locked_setting_can_be_taken_over(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    with pytest.raises(DomainError) as caught:
        await _service(uow, clock).adopt(SUPER, "smtp_host")

    assert caught.value.code is ErrorCode.SETTING_NOT_LOCKED


async def test_the_cli_never_sets_a_secret(uow: InMemoryUnitOfWork, clock: FixedClock) -> None:
    with pytest.raises(DomainError) as caught:
        await _service(uow, clock).change_host("smtp_password", "s3cret")

    assert caught.value.code is ErrorCode.SETTING_NOT_EDITABLE
    assert uow.state.platform_secrets == {}


# The login mapping after a dry run (ADR 0042 step 7, §8).

MAPPING = {
    "oidc_role_claim": "realm_roles",
    "oidc_organisation_claim": "organisations",
    "oidc_group_claim": "groups",
    "oidc_role_superuser": "op-super",
    "oidc_role_org_admin": "op-org",
    "oidc_role_group_admin": "op-group",
    "oidc_role_auditor": "op-audit",
    "oidc_role_agent": "op-agent",
    "oidc_entitlement_claim": "op_entitlements",
    "oidc_entitlement_prefix": "op-acc",
}


async def test_a_mapping_that_keeps_the_superuser_is_saved_and_audited(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    await _service(uow, clock).change_login_mapping(SUPER, MAPPING, trial=SUPER)

    assert uow.state.platform_settings["oidc_role_claim"] == "realm_roles"
    assert uow.state.settings_version == 1
    actions = {e.action for e in uow.state.audit if e.outcome is Outcome.SUCCESS}
    assert actions == {"settings.login.manage"}
    assert "set:oidc_role_superuser:openplanner-superuser->op-super" in {
        e.detail for e in uow.state.audit
    }


async def test_a_mapping_that_would_lock_the_superuser_out_is_refused(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    no_roles = principal("sub-super", "S", frozenset(), frozenset(), frozenset())

    with pytest.raises(DomainError) as caught:
        await _service(uow, clock).change_login_mapping(SUPER, MAPPING, trial=no_roles)

    assert caught.value.code is ErrorCode.LOGIN_LOCKOUT
    assert uow.state.platform_settings == {}
    assert uow.state.audit[-1].reason == "login_lockout"


async def test_a_dry_run_of_another_person_does_not_count(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    other = principal("sub-other", "O", frozenset({Role.SUPERUSER}), frozenset(), frozenset())

    with pytest.raises(DomainError) as caught:
        await _service(uow, clock).change_login_mapping(SUPER, MAPPING, trial=other)

    assert caught.value.code is ErrorCode.LOGIN_LOCKOUT


async def test_only_the_superuser_changes_the_login_mapping(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    with pytest.raises(DomainError) as caught:
        await _service(uow, clock).change_login_mapping(AUDITOR, MAPPING, trial=AUDITOR)

    assert caught.value.code is ErrorCode.NOT_PERMITTED


async def test_a_locked_key_of_the_mapping_refuses_the_whole_change(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    service = _service(uow, clock, locked=frozenset({"oidc_group_claim"}))

    with pytest.raises(DomainError) as caught:
        await service.change_login_mapping(SUPER, MAPPING, trial=SUPER)

    assert caught.value.code is ErrorCode.SETTING_LOCKED
    assert uow.state.platform_settings == {}


async def test_the_host_can_set_and_reset_the_mapping_to_recover(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    service = _service(uow, clock)

    await service.change_host("oidc_role_claim", "realm_roles")
    assert uow.state.platform_settings == {"oidc_role_claim": "realm_roles"}
    assert await service.reset_host("oidc_role_claim")


# Webex and WxCC (ADR 0042 step 6b-1).


def test_the_webex_service_app_and_the_wxcc_host_are_editable() -> None:
    for key in (
        "webex_client_id",
        "webex_client_secret",
        "webex_refresh_token",
        "wxcc_api_base_url",
    ):
        assert editable(BY_KEY[key]), key


def test_webhook_secrets_are_never_typed_in() -> None:
    # ADR 0042 inventory: generated by the server and rotated with a button (step 6b-2).
    for key in ("webex_webhook_secret", "wxcc_webhook_secret"):
        assert not editable(BY_KEY[key]), key


async def test_the_wxcc_host_must_use_https(uow: InMemoryUnitOfWork, clock: FixedClock) -> None:
    with pytest.raises(DomainError) as caught:
        await _service(uow, clock).change(SUPER, "wxcc_api_base_url", "http://api.wxcc.example")

    assert caught.value.code is ErrorCode.SETTING_INVALID


async def test_production_refuses_the_rc_channel_and_auto_apply(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """ADR 0067: the portal cannot move a production installation onto candidates."""
    production = SettingsService(
        uow,
        clock,
        AdminService(uow, clock),
        locked=lambda: set(),
        combination=lambda _: None,
        production=True,
    )
    with pytest.raises(DomainError, match="setting_invalid"):
        await production.change(SUPER, "update_channel", "rc")
    with pytest.raises(DomainError, match="setting_invalid"):
        await production.change(SUPER, "update_auto_apply", "true")
    await production.change(SUPER, "update_channel", "release")

    # Outside production both are ordinary class C changes.
    await _service(uow, clock).change(SUPER, "update_channel", "rc")
    await _service(uow, clock).change(SUPER, "update_auto_apply", "true")
    async with uow.read() as tx:
        stored = await tx.platform_settings()
    assert (stored["update_channel"], stored["update_auto_apply"]) == ("rc", True)


# ADR 0085 point 8: TRACE falls back sooner than the other levels.


async def test_trace_expires_within_trace_level_max_hours(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    service = _service(uow, clock)

    with pytest.raises(DomainError) as caught:
        await service.change(SUPER, "log_level", "TRACE", hours=5)  # the default cap is 4
    assert caught.value.code is ErrorCode.SETTING_EXPIRY

    await service.change(SUPER, "log_level", "TRACE", hours=4)
    assert uow.state.platform_expiries == {"log_level": clock.now() + timedelta(hours=4)}


async def test_the_trace_cap_follows_the_setting_from_the_portal_or_the_environment(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    await _service(uow, clock).change(SUPER, "trace_level_max_hours", "2")
    with pytest.raises(DomainError):
        await _service(uow, clock).change(SUPER, "log_level", "TRACE", hours=3)

    locked = _service(uow, clock, environment={"trace_level_max_hours": 1})
    with pytest.raises(DomainError):
        await locked.change(SUPER, "log_level", "TRACE", hours=2)
    await locked.change(SUPER, "log_level", "TRACE", hours=1)
    # DEBUG keeps the general cap of 24 hours.
    await locked.change(SUPER, "log_level", "DEBUG", hours=24)
