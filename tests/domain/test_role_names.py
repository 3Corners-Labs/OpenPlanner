"""The names of the organisation admin and the group admin (ADR 0077)."""

import pytest

from app.domain.agent_term import MAX_TERM_LENGTH
from app.domain.errors import DomainError, ErrorCode
from app.domain.role_names import ROLE_NAME_KEYS, clean_role_name

ORG, GROUP = ROLE_NAME_KEYS


def test_a_name_is_trimmed() -> None:
    assert clean_role_name(ORG, "  Hoofdbeheer ") == "Hoofdbeheer"


@pytest.mark.parametrize(
    "text", ["", " ", "x", "x" * (MAX_TERM_LENGTH + 1), "twee\nregels", "met {agent}", "bel\x07"]
)
def test_a_name_that_breaks_a_rule_is_refused_with_its_key(text: str) -> None:
    with pytest.raises(DomainError) as refused:
        clean_role_name(GROUP, text)

    assert refused.value.code is ErrorCode.INVALID_CONFIGURATION
    assert refused.value.field == GROUP


def test_a_name_may_not_be_the_name_of_another_role_whatever_the_case() -> None:
    taken = ["Groepsbeheerder", "Auditor"]

    with pytest.raises(DomainError) as refused:
        clean_role_name(ORG, "groepsBEHEERDER", taken)

    assert refused.value.field == ORG
    assert clean_role_name(ORG, "Hoofdbeheer", taken) == "Hoofdbeheer"


@pytest.mark.parametrize("key", ["authz.role.superuser", "authz.role.auditor", "term.agent", "x"])
def test_only_the_two_roles_of_an_organisation_can_be_named(key: str) -> None:
    with pytest.raises(DomainError) as refused:
        clean_role_name(key, "Eigen naam")

    assert refused.value.field == "key"
