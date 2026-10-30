from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.domain.event_schema import Envelope
from app.i18n.dates import ClockStyle


def test_defaults_use_sqlite_in_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = Settings(environment="dev")

    assert settings.database_url == "sqlite+aiosqlite:///data/booking.db"


def test_reads_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://db/booking")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("ENVIRONMENT", "dev")

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://db/booking"
    assert settings.log_level == "DEBUG"


def test_clock_times_default_to_24_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TIME_FORMAT", raising=False)

    assert Settings(environment="dev").time_format is ClockStyle.H24


def test_time_format_is_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIME_FORMAT", "12h")

    assert Settings(environment="dev").time_format is ClockStyle.H12


def test_rejects_unknown_time_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIME_FORMAT", "am-pm")

    with pytest.raises(ValueError, match="time_format"):
        Settings(environment="dev")


def test_rejects_unknown_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "chatty")
    monkeypatch.setenv("ENVIRONMENT", "dev")

    with pytest.raises(ValueError, match="log_level"):
        Settings()


def test_production_is_the_default_and_refuses_development_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("ENVIRONMENT", "MANAGEMENT_TOKEN_KEY", "PUBLIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="MANAGEMENT_TOKEN_KEY"):
        Settings()
    with pytest.raises(ValueError, match="https"):
        Settings(management_token_key="k" * 32)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="SESSION_SECRET"):
        Settings(
            management_token_key="k" * 32,  # type: ignore[arg-type]
            public_base_url="https://afspraak.example.nl",
        )
    production = {
        "management_token_key": "k" * 32,
        "public_base_url": "https://afspraak.example.nl",
        "session_secret": "s" * 32,
    }
    with pytest.raises(ValueError, match="OIDC"):
        Settings(**production, oidc_discovery_url="http://idp.local/.well-known")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="SIMULATOR_ENABLED"):
        Settings(**production, simulator_enabled=True)  # type: ignore[arg-type]
    # ADR 0059: production follows releases, on a superuser's click.
    with pytest.raises(ValueError, match="UPDATE_CHANNEL"):
        Settings(**production, update_channel="rc")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="UPDATE_AUTO_APPLY"):
        Settings(**production, update_auto_apply=True)  # type: ignore[arg-type]

    settings = Settings(**production)  # type: ignore[arg-type]
    assert settings.environment == "prod"
    assert not settings.simulator_enabled


@pytest.mark.parametrize(
    ("field", "key"),
    [("oidc_client_secret", "OIDC_CLIENT_SECRET"), ("session_secret", "SESSION_SECRET")],
)
def test_production_refuses_development_placeholder_secrets(field: str, key: str) -> None:
    # .env.example and the dev Keycloak realm share "dev-only-openplanner-admin-secret".
    production = {
        "environment": "prod",
        "management_token_key": "m" * 40,
        "session_secret": "s" * 40,
        "public_base_url": "https://afspraak.example.nl",
        "oidc_discovery_url": "https://idp.example.nl/.well-known/openid-configuration",
        "oidc_client_secret": "real-client-secret",
    }
    placeholder = "dev-only-openplanner-admin-secret"
    with pytest.raises(ValidationError, match=key) as refused:
        Settings(**{**production, field: placeholder})  # type: ignore[arg-type]
    assert placeholder not in str(refused.value)

    assert Settings(environment="dev", **{field: placeholder}).environment == "dev"  # type: ignore[arg-type]
    assert Settings(**production).environment == "prod"  # type: ignore[arg-type]


def test_the_simulator_is_off_unless_switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIMULATOR_ENABLED", raising=False)
    assert not Settings(environment="dev").simulator_enabled
    monkeypatch.setenv("SIMULATOR_ENABLED", "true")
    assert Settings(environment="test").simulator_enabled


def test_production_webex_needs_secrets_and_the_encryption_key() -> None:
    production = {
        "environment": "prod",
        "management_token_key": "m" * 40,
        "session_secret": "s" * 40,
        "public_base_url": "https://afspraak.example.nl",
    }
    with pytest.raises(ValueError, match="WEBEX_CLIENT_SECRET"):
        Settings(**production, webex_client_id="client")  # type: ignore[arg-type]
    configured = Settings(
        **production,  # type: ignore[arg-type]
        webex_client_id="client",
        webex_client_secret="secret",
        webex_refresh_token="refresh",
        settings_encryption_key="e" * 40,
    )
    assert configured.video_configured
    assert not configured.dispatch_configured


def test_webhooks_can_use_their_own_https_base_url() -> None:
    production = {
        "environment": "prod",
        "management_token_key": "m" * 40,
        "session_secret": "s" * 40,
        "public_base_url": "https://afspraak.example.nl",
    }
    with pytest.raises(ValueError, match="WEBHOOK_BASE_URL"):
        Settings(**production, webhook_base_url="http://tunnel.example.nl")  # type: ignore[arg-type]

    assert Settings(**production).webhook_base == "https://afspraak.example.nl"  # type: ignore[arg-type]
    dev = Settings(
        environment="dev",
        public_base_url="http://127.0.0.1:8000",
        webhook_base_url="https://abc.trycloudflare.com/",
    )
    assert dev.webhook_base == "https://abc.trycloudflare.com"
    assert dev.public_base_url == "http://127.0.0.1:8000"  # OIDC redirect and links unchanged


def test_validation_errors_never_contain_the_input(monkeypatch: pytest.MonkeyPatch) -> None:
    test_pw = "never-in-an-error"  # noqa: S105 - test value
    monkeypatch.setenv("SMTP_HOST", "smtp.example.nl")
    monkeypatch.setenv("SMTP_PORT", "25")
    monkeypatch.setenv("SMTP_STARTTLS", "false")
    monkeypatch.delenv("SMTP_TLS", raising=False)
    monkeypatch.setenv("SMTP_USER", "planner@example.nl")
    monkeypatch.setenv("SMTP_USER_PASSWORD", test_pw)

    with pytest.raises(ValidationError) as caught:
        Settings(environment="dev")

    assert test_pw not in str(caught.value)
    assert test_pw not in repr(caught.value)
    assert "input_value" not in str(caught.value)


def test_update_repository_must_be_owner_and_name() -> None:
    assert Settings(environment="dev", update_repository="acme/open-planner").update_repository
    for bad in ("acme", "acme/x/y", "../etc", "acme/x?y=1"):
        with pytest.raises(ValidationError):
            Settings(environment="dev", update_repository=bad)


def test_storage_limits_must_be_ordered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_LOW_FREE_PERCENT", "5")
    monkeypatch.setenv("STORAGE_CRITICAL_FREE_PERCENT", "10")

    with pytest.raises(ValidationError, match="STORAGE_CRITICAL_FREE_PERCENT"):
        Settings(environment="dev")


def test_the_installation_name_and_version_go_into_every_record() -> None:
    # ADR 0040, part 2 (class B): names this installation on a shared log platform.
    settings = Settings(environment="dev", app_version="1.4.0", installation_name="gemeente-prod")

    assert settings.event_envelope == Envelope("1.4.0", "gemeente-prod")
    assert Settings(environment="dev").event_envelope == Envelope("", "")


def test_the_installation_name_is_a_plain_token() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="dev", installation_name='prod\n{"x":1}')


@pytest.mark.parametrize("minutes", [0, 1441])
def test_the_security_event_window_has_bounds(minutes: int) -> None:
    # ADR 0034, AU-4 (class C): 1 minute to 1 day, default 15.
    assert Settings(environment="dev").security_event_window_minutes == 15
    with pytest.raises(ValidationError):
        Settings(environment="dev", security_event_window_minutes=minutes)


def test_the_build_commit_must_be_a_git_sha() -> None:
    # ADR 0052: APP_COMMIT is baked in by main.yml; anything else is a broken build.
    assert Settings(environment="dev", app_commit="ce5602a").app_commit == "ce5602a"
    assert Settings(environment="dev").app_commit == ""
    with pytest.raises(ValidationError):
        Settings(environment="dev", app_commit="main; rm -rf /")


def test_each_category_has_its_own_retention() -> None:
    # ADR 0040, part 4 (class C): admin actions 2 years (product owner, 2026-10-29).
    retention = Settings(environment="dev").event_retention

    assert (retention.audit, retention.system) == (timedelta(days=730), timedelta(days=90))
    assert (retention.security, retention.diagnostic) == (timedelta(days=365), timedelta(days=7))
    with pytest.raises(ValidationError):
        Settings(environment="dev", audit_retention_days=364)
    with pytest.raises(ValidationError):
        Settings(environment="dev", diagnostic_mode_hours=73)


def test_a_development_installation_lists_what_production_would_refuse() -> None:
    problems = Settings(environment="dev", simulator_enabled=True).production_problems()

    assert [p.code for p in problems] == [
        "simulator_enabled",
        "management_token_key",
        "public_base_url",
        "session_secret",
    ]
    assert problems[0].variable == "SIMULATOR_ENABLED"
    assert problems[0].message == "SIMULATOR_ENABLED is not allowed in production"


def test_production_refuses_with_the_first_problem_in_the_same_words() -> None:
    with pytest.raises(ValueError, match="MANAGEMENT_TOKEN_KEY is required in production"):
        Settings(environment="prod")
