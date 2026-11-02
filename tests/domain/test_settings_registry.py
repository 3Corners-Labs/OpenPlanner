"""The settings registry: classes, permissions, bounds and lookup (ADR 0042)."""

import pytest

from app.domain.settings_registry import (
    BY_KEY,
    LOGIN_MANAGE,
    ORGANISATION_MANAGE,
    PLATFORM_MANAGE,
    REGISTRY,
    Apply,
    Kind,
    SettingClass,
    bounds_text,
    lookup,
    parse_rate,
    within_bounds,
)


def test_keys_are_unique_and_lower_case() -> None:
    keys = [spec.key for spec in REGISTRY]

    assert len(keys) == len(set(keys))
    assert all(key == key.lower() for key in keys)


def test_every_default_lies_within_the_portal_bounds() -> None:
    outside = [spec.key for spec in REGISTRY if not within_bounds(spec, spec.default)]

    assert outside == []


@pytest.mark.parametrize(
    ("key", "permission"),
    [
        ("rate_limit_bookings", PLATFORM_MANAGE),
        ("oidc_role_claim", LOGIN_MANAGE),
        ("oidc_role_auditor", LOGIN_MANAGE),
        ("time_format", ORGANISATION_MANAGE),
        ("session_secret", None),
        ("rate_limit_enabled", None),
    ],
)
def test_the_permission_follows_class_and_topic(key: str, permission: str | None) -> None:
    assert BY_KEY[key].permission == permission


def test_only_portal_settings_are_lockable_and_live() -> None:
    for spec in REGISTRY:
        portal = spec.class_ in (SettingClass.PLATFORM, SettingClass.ORGANISATION)
        assert spec.lockable is portal
        assert (spec.apply is Apply.LIVE) is portal
        assert (spec.manual_topic is not None) is portal


def test_kill_switches_and_keys_never_reach_the_portal() -> None:
    for key in (
        "rate_limit_enabled",
        "agent_api_enabled",
        "simulator_enabled",
        "settings_encryption_key",
        "settings_encryption_key_previous",
        "management_token_key",
        "management_token_key_previous",
        "session_secret",
        "oidc_discovery_url",
        "oidc_client_secret",
    ):
        assert not BY_KEY[key].in_portal, key


def test_security_floors_of_section_7() -> None:
    expected = {
        "admin_session_hours": (1, 8),
        "admin_reauth_minutes": (5, 60),
        "rate_limit_bookings": (1, 30),
        "rate_limit_reads": (10, 300),
        "rate_limit_unknown_links": (5, 100),
        "rate_limit_agent_tokens": (1, 30),
        "rate_limit_agent_calls": (10, 600),
        "system_event_retention_days": (30, 3650),
    }

    assert {k: (BY_KEY[k].minimum, BY_KEY[k].maximum) for k in expected} == expected


def test_secrets_are_secret_and_others_are_not() -> None:
    assert BY_KEY["smtp_password"].secret
    assert BY_KEY["smtp_password"].kind is Kind.SECRET
    assert not BY_KEY["smtp_username"].secret


@pytest.mark.parametrize(
    ("value", "parsed"),
    [
        ("10/hour", (10, "hour")),
        ("10 per hour", (10, "hour")),
        ("60/minutes", (60, "minute")),
        ("ten/hour", None),
        ("10/fortnight", None),
    ],
)
def test_parse_rate(value: str, parsed: tuple[int, str] | None) -> None:
    assert parse_rate(value) == parsed


@pytest.mark.parametrize(
    ("key", "value", "inside"),
    [
        ("rate_limit_bookings", "30/hour", True),
        ("rate_limit_bookings", "31/hour", False),
        ("rate_limit_bookings", "0/hour", False),
        ("rate_limit_bookings", "10/minute", False),
        ("rate_limit_bookings", "nonsense", False),
        ("system_event_retention_days", 7, False),
        ("system_event_retention_days", 30, True),
        ("worker_poll_seconds", 4.9, False),
        ("worker_poll_seconds", 300.0, True),
        ("database_max_mb", None, True),
        ("database_max_mb", 99, False),
        ("database_max_mb", 100_000, True),
        ("log_level", "DEBUG", True),
        ("log_level", "VERBOSE", False),
        ("wxcc_api_base_url", "http://api.wxcc-eu1.cisco.com", False),
        ("wxcc_api_base_url", "https://api.wxcc-eu1.cisco.com", True),
        ("admin_timezone", "Europe/Amsterdam", True),
        ("smtp_port", None, False),
    ],
)
def test_within_bounds(key: str, value: object, inside: bool) -> None:
    assert within_bounds(BY_KEY[key], value) is inside


@pytest.mark.parametrize(
    ("key", "text"),
    [
        ("rate_limit_bookings", "1–30 per hour"),
        ("worker_poll_seconds", "5–300"),
        ("database_max_mb", "empty or ≥ 100"),
        ("log_level", "TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL"),
        ("wxcc_api_base_url", "https only"),
        ("mail_from", ""),
    ],
)
def test_bounds_text(key: str, text: str) -> None:
    assert bounds_text(BY_KEY[key]) == text


def test_lookup_by_variable_alias_or_case() -> None:
    assert lookup("RATE_LIMIT_BOOKINGS") is BY_KEY["rate_limit_bookings"]
    assert lookup(" smtp_user ") is BY_KEY["smtp_username"]
    assert lookup("SMTP_USER_PASSWORD") is BY_KEY["smtp_password"]
    assert lookup("nope") is None


def test_label_and_hint_keys() -> None:
    spec = BY_KEY["mail_from"]

    assert spec.label_key == "settings.field.mail_from.label"
    assert spec.hint_key == "settings.field.mail_from.hint"
    assert spec.env == "MAIL_FROM"
