"""``settings list/show``: source, lock, secrets and the bounds flag (ADR 0042 §10)."""

from pydantic import SecretStr

from app.adapters.settings_report import (
    SettingState,
    describe,
    effective,
    format_list,
    format_one,
)
from app.config import Settings
from app.domain.platform_settings import PortalValues

SECRET = "smtp-password-never-printed"  # noqa: S105 - test value


def _state(settings: Settings, environ: dict[str, str], key: str) -> SettingState:
    return next(s for s in describe(settings, environ) if s.spec.key == key)


def test_an_unset_setting_comes_from_the_default() -> None:
    state = _state(Settings(environment="dev"), {}, "rate_limit_bookings")

    assert (state.source, state.locked, state.shown) == ("default", False, "10/hour")


def test_an_env_value_locks_a_portal_setting() -> None:
    settings = Settings(environment="dev", rate_limit_bookings="20/hour")
    state = _state(settings, {"RATE_LIMIT_BOOKINGS": "20/hour"}, "rate_limit_bookings")

    assert (state.source, state.locked, state.shown) == ("env", True, "20/hour")
    assert not state.outside_bounds


def test_env_only_settings_are_never_locked() -> None:
    state = _state(Settings(environment="dev"), {"ENVIRONMENT": "dev"}, "environment")

    assert (state.source, state.locked) == ("env", False)


def test_an_alias_counts_as_the_variable() -> None:
    settings = Settings(environment="dev", smtp_username="mailer")
    state = _state(settings, {"smtp_user": "mailer"}, "smtp_username")

    assert (state.source, state.locked) == ("env", True)


def test_an_env_value_outside_the_portal_bounds_is_flagged_not_refused() -> None:
    settings = Settings(environment="dev", system_event_retention_days=7)
    state = _state(settings, {"SYSTEM_EVENT_RETENTION_DAYS": "7"}, "system_event_retention_days")

    assert state.outside_bounds
    assert "! outside portal bounds" in format_list([state])
    assert "warning:" in format_one(state)


def test_a_default_is_never_flagged() -> None:
    assert not any(s.outside_bounds for s in describe(Settings(environment="dev"), {}))


def test_secrets_show_only_whether_they_are_set() -> None:
    settings = Settings(environment="dev", smtp_password=SecretStr(SECRET))
    states = describe(settings, {"SMTP_PASSWORD": SECRET})
    text = format_list(states) + "".join(format_one(s) for s in states)

    assert _state(settings, {}, "smtp_password").shown == "set"
    assert _state(settings, {}, "session_secret").shown == "not set"
    assert SECRET not in text


def test_proxy_variables_are_read_from_the_environment() -> None:
    state = _state(Settings(environment="dev"), {"TLS_DOMAIN": "a.example.nl"}, "tls_domain")

    assert (state.source, state.shown) == ("env", "a.example.nl")


def test_show_lists_the_details() -> None:
    text = format_one(_state(Settings(environment="dev"), {}, "rate_limit_bookings"))

    assert "portal bounds: 1–30 per hour" in text
    assert "permission:    settings.platform.manage" in text
    assert "class:         C (platform)" in text


def test_a_secret_from_its_file_shows_the_file_as_source() -> None:
    settings = Settings(environment="dev", session_secret=SecretStr(SECRET))
    state = _state(settings, {"SESSION_SECRET_FILE": "/run/secrets/session"}, "session_secret")

    assert (state.source, state.shown) == ("file", "set")


# Portal values (ADR 0042 step 3).

PORTAL = PortalValues(5, {"admin_session_hours": 4, "rate_limit_bookings": "99/hour"})


def test_a_portal_value_is_reported_with_the_portal_as_source() -> None:
    state = next(
        s
        for s in describe(Settings(environment="dev"), {}, PORTAL)
        if s.spec.key == "admin_session_hours"
    )

    assert (state.source, state.locked, state.shown) == ("portal", False, "4")


def test_a_portal_value_outside_the_rules_is_reported_as_ignored() -> None:
    state = next(
        s
        for s in describe(Settings(environment="dev"), {}, PORTAL)
        if s.spec.key == "rate_limit_bookings"
    )

    assert (state.source, state.shown, state.portal_ignored) == ("default", "10/hour", True)
    assert "! stored portal value ignored" in format_list([state])


def test_the_environment_wins_over_the_portal() -> None:
    settings = Settings(environment="dev", admin_session_hours=6)
    state = next(
        s
        for s in describe(settings, {"ADMIN_SESSION_HOURS": "6"}, PORTAL)
        if s.spec.key == "admin_session_hours"
    )

    assert (state.source, state.locked, state.shown) == ("env", True, "6")


def test_the_effective_settings_take_unlocked_portal_values_only() -> None:
    settings = Settings(environment="dev", admin_reauth_minutes=30)
    portal = PortalValues(
        2, {"admin_session_hours": 4, "admin_reauth_minutes": 10, "public_base_url": "https://x"}
    )

    current = effective(settings, {"ADMIN_REAUTH_MINUTES": "30"}, portal)

    assert current.admin_session_hours == 4
    assert current.admin_reauth_minutes == 30  # locked by the environment
    assert current.public_base_url == settings.public_base_url  # class B: never the portal
    assert settings.admin_session_hours == 8  # the base is left alone


def test_a_sealed_portal_secret_reaches_the_effective_settings_as_a_secret() -> None:
    portal = PortalValues(3, secrets={"smtp_password": SECRET})

    current = effective(Settings(environment="dev"), {}, portal)
    state = next(
        s
        for s in describe(Settings(environment="dev"), {}, portal)
        if s.spec.secret and s.spec.key == "smtp_password"
    )

    assert isinstance(current.smtp_password, SecretStr)
    assert current.smtp_password.get_secret_value() == SECRET
    assert (state.source, state.shown, state.value) == ("portal", "set", None)
