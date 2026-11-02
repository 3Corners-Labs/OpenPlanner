"""User names under the platform suffix (ADR 0106 points 5 and 6)."""

import pytest

from app.domain.authorization import Grant
from app.domain.entitlements import Directory, OrganisationRef
from app.domain.user_names import (
    COLLISION_LIMIT,
    DEFAULT_SUFFIX,
    FALLBACK_LOCAL_PART,
    LOCAL_PART_MAX,
    candidates,
    organisation_label,
    propose_local_part,
    user_name,
    valid_local_part,
    valid_suffix,
)

PLACES = Directory({}, (OrganisationRef("demo", "demo"), OrganisationRef("bd", "belastingdienst")))


@pytest.mark.parametrize(
    ("first", "last", "expected"),
    [
        ("Jan", "Jansen", "jan.jansen"),
        ("Zoë", "de Vries", "zoe.de.vries"),
        ("Anne-Marie", "Çelik", "anne-marie.celik"),
        ("  Piet ", "O'Brien", "piet.obrien"),
        ("李", "王", FALLBACK_LOCAL_PART),
    ],
)
def test_the_proposal_is_first_dot_last_in_plain_lower_case(
    first: str, last: str, expected: str
) -> None:
    assert propose_local_part(first, last) == expected


def test_a_long_name_is_cut_on_a_clean_edge() -> None:
    proposal = propose_local_part("a" * 40, "b" * 40)

    assert len(proposal) <= LOCAL_PART_MAX
    assert valid_local_part(proposal)


@pytest.mark.parametrize("value", ["jan", "jan.jansen", "j-2", "a" * LOCAL_PART_MAX])
def test_valid_local_parts(value: str) -> None:
    assert valid_local_part(value)


@pytest.mark.parametrize(
    "value", ["", "Jan", ".jan", "jan.", "jan..jansen", "jan jansen", "jan@x", "a" * 65, "-jan"]
)
def test_invalid_local_parts(value: str) -> None:
    assert not valid_local_part(value)


@pytest.mark.parametrize("value", [DEFAULT_SUFFIX, "staff.example.nl", "a.b"])
def test_valid_suffixes(value: str) -> None:
    assert valid_suffix(value)


@pytest.mark.parametrize(
    "value",
    ["internal", "Openplanner.internal", "-x.internal", "a..b", ".".join(["a"] * 11), "a." * 96],
)
def test_invalid_suffixes(value: str) -> None:
    assert not valid_suffix(value)


def test_the_label_is_the_idp_key_of_the_first_grant_s_organisation() -> None:
    assert organisation_label([Grant("org_admin", "bd")], PLACES) == "belastingdienst"
    assert organisation_label([Grant("agent", "demo", "team")], PLACES) == "demo"


def test_a_grant_that_reaches_everything_has_no_label() -> None:
    assert organisation_label([Grant("superuser")], PLACES) is None


def test_the_name_joins_the_parts() -> None:
    assert user_name("jan", "demo", DEFAULT_SUFFIX) == "jan@demo.openplanner.internal"
    assert user_name("jan", None, DEFAULT_SUFFIX) == "jan@openplanner.internal"


def test_alternatives_count_up_and_stay_within_the_limit() -> None:
    names = list(candidates("a" * LOCAL_PART_MAX, "demo", DEFAULT_SUFFIX))

    assert len(names) == COLLISION_LIMIT
    assert names[1].startswith("a" * (LOCAL_PART_MAX - 2) + "-2@")
    assert all(valid_local_part(name.split("@")[0]) for name in names)
