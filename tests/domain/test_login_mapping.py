"""The login mapping: claim names and role values, checked before a dry run (ADR 0042 §8)."""

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.login_mapping import LOGIN_KEYS, validate_mapping

GOOD = {
    "oidc_role_claim": "roles",
    "oidc_organisation_claim": "organisations",
    "oidc_group_claim": "groups",
    "oidc_role_superuser": "openplanner-superuser",
    "oidc_role_org_admin": "openplanner-org-admin",
    "oidc_role_group_admin": "openplanner-group-admin",
    "oidc_role_auditor": "openplanner-auditor",
    "oidc_role_agent": "openplanner-agent",
}


def test_the_mapping_has_the_eight_registered_keys() -> None:
    assert set(LOGIN_KEYS) == set(GOOD)


def test_a_valid_mapping_is_returned_trimmed() -> None:
    assert (
        validate_mapping({**GOOD, "oidc_role_claim": "  realm_access.roles "})["oidc_role_claim"]
        == "realm_access.roles"
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("oidc_role_claim", ""),
        ("oidc_group_claim", "groups; drop"),
        ("oidc_role_superuser", "a" * 129),
        ("oidc_role_auditor", "openplanner-superuser"),  # two roles with one value
    ],
)
def test_an_invalid_mapping_is_refused_by_key(key: str, value: str) -> None:
    with pytest.raises(DomainError) as caught:
        validate_mapping({**GOOD, key: value})

    assert caught.value.code is ErrorCode.SETTING_INVALID
    assert caught.value.field in LOGIN_KEYS


def test_a_missing_key_is_refused() -> None:
    values = dict(GOOD)
    del values["oidc_role_agent"]

    with pytest.raises(DomainError):
        validate_mapping(values)
