"""``settings list/show``: source, lock, secrets and the bounds flag (ADR 0042 §10)."""

from pydantic import SecretStr

from app.adapters.settings_report import SettingState, describe, format_list, format_one
from app.config import Settings

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
