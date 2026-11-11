"""Subjects, routing attributes and the routing mapping of a group (ADR 0081)."""

from pathlib import Path

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.routing import (
    ATTRIBUTE_VALUES,
    SUBJECT_SOURCE,
    Attribute,
    GroupRouting,
    RouteVariable,
    RoutingChoice,
    Subject,
    check_subject_count,
    validate_label,
    validate_labels,
)


def subject(code: str = "general", nl: str = "Algemeen", en: str = "General") -> Subject:
    """Return a subject with Dutch and English labels."""
    return Subject.create(code, {"nl": nl, "en": en})


def routing() -> GroupRouting:
    """Return a routing with two subjects and both attributes switched on."""
    return GroupRouting(
        (subject("general"), subject("move", "Verhuizen", "Moving")),
        (Attribute.PREFERRED_LANGUAGE, Attribute.CHANNEL_PREFERENCE),
    )


def test_a_subject_needs_a_code_of_the_grammar_and_both_labels() -> None:
    assert subject().label("en") == "General"
    assert subject().label("de") == "Algemeen"
    for code in ("", "Upper", "-lead", "a" * 41, "with space"):
        with pytest.raises(DomainError) as refused:
            subject(code)
        assert refused.value.field == "subject_code"
    with pytest.raises(DomainError):
        Subject.create("general", {"nl": "Algemeen"})
    with pytest.raises(DomainError):
        subject(nl="  ")
    with pytest.raises(DomainError):
        subject(en="line\nbreak")


def test_the_catalogue_offers_the_locales_with_a_catalogue() -> None:
    assert set(ATTRIBUTE_VALUES[Attribute.PREFERRED_LANGUAGE]) == {
        p.stem for p in Path("app/i18n/locales").glob("*.po")
    }
    assert ATTRIBUTE_VALUES[Attribute.CHANNEL_PREFERENCE] == ("video", "phone")


def test_duplicate_codes_and_variables_are_refused() -> None:
    with pytest.raises(DomainError):
        GroupRouting((subject(), subject()))
    variable = RouteVariable("topic", (("general", "G"),))
    with pytest.raises(DomainError):
        GroupRouting(
            (subject(),),
            (Attribute.PREFERRED_LANGUAGE,),
            ((SUBJECT_SOURCE, variable), ("preferred_language", RouteVariable("topic"))),
        )


@pytest.mark.parametrize("name", ["", "1st", "has-dash", "openplannerGroup", "x" * 65])
def test_a_variable_name_follows_the_grammar(name: str) -> None:
    with pytest.raises(DomainError) as refused:
        RouteVariable(name)
    assert refused.value.field == "variable_name"


@pytest.mark.parametrize("value", ["", "two words", "<script>", "a" * 65, "é"])
def test_a_value_is_a_code_never_free_text(value: str) -> None:
    with pytest.raises(DomainError) as refused:
        RouteVariable("topic", (("general", value),))
    assert refused.value.field == "variable_value"


def test_a_mapping_may_only_name_codes_of_its_source() -> None:
    with pytest.raises(DomainError):
        GroupRouting((subject(),), (), ((SUBJECT_SOURCE, RouteVariable("t", (("x", "1"),))),))
    with pytest.raises(DomainError):
        GroupRouting((), (), (("preferred_language", RouteVariable("lang")),))


def test_choose_takes_one_of_the_codes_per_question() -> None:
    choice = routing().choose(
        {"_subject": "move", "_preferred_language": "en", "_channel_preference": "phone"}
    )
    assert choice.subject == "move"
    assert choice.attribute(Attribute.PREFERRED_LANGUAGE) == "en"
    assert choice.attribute(Attribute.CHANNEL_PREFERENCE) == "phone"


@pytest.mark.parametrize(
    ("answers", "field"),
    [
        ({"_preferred_language": "en", "_channel_preference": "video"}, "_subject"),
        ({"_subject": "Moving", "_preferred_language": "en"}, "_subject"),
        ({"_subject": "move", "_preferred_language": "de"}, "_preferred_language"),
        ({"_subject": "move", "_preferred_language": "en", "_channel_preference": "x"}, None),
    ],
)
def test_a_crafted_answer_is_refused_by_its_key_only(
    answers: dict[str, str], field: str | None
) -> None:
    with pytest.raises(DomainError) as refused:
        routing().choose(answers)
    assert refused.value.code is ErrorCode.INVALID_ANSWER
    if field:
        assert refused.value.field == field
    assert "Moving" not in str(refused.value)


def test_a_group_without_routing_asks_nothing() -> None:
    assert GroupRouting().choose({"_subject": "anything"}).empty
    assert GroupRouting().sources == ()


def test_variables_send_only_mapped_codes() -> None:
    mapped = (
        routing()
        .with_mapping(SUBJECT_SOURCE, RouteVariable("topic", (("move", "MV"),)))
        .with_mapping("preferred_language", RouteVariable("lang", (("en", "en-GB"),)))
    )
    choice = RoutingChoice("move", (("channel_preference", "phone"), ("preferred_language", "en")))
    assert mapped.variables(choice) == {"topic": "MV", "lang": "en-GB"}
    # An unmapped code (general) and an unmapped source (channel) are not sent.
    assert mapped.variables(RoutingChoice("general", (("preferred_language", "nl"),))) == {}
    assert mapped.variables(None) == {}
    # A code the group no longer has is not sent either.
    assert mapped.variables(RoutingChoice("gone")) == {}


def test_removing_a_subject_or_attribute_drops_its_mapping() -> None:
    mapped = routing().with_mapping(
        SUBJECT_SOURCE, RouteVariable("topic", (("move", "MV"), ("general", "GE")))
    )
    fewer = mapped.with_subjects((subject("general"),))
    assert fewer.variable(SUBJECT_SOURCE) == RouteVariable("topic", (("general", "GE"),))
    lang = mapped.with_mapping("preferred_language", RouteVariable("lang"))
    assert lang.with_attributes(()).variable("preferred_language") is None
    assert lang.with_mapping("preferred_language", None).variable("preferred_language") is None
    with pytest.raises(DomainError):
        GroupRouting().with_mapping(SUBJECT_SOURCE, RouteVariable("topic"))


def test_attributes_keep_catalogue_order() -> None:
    switched = GroupRouting().with_attributes(
        {Attribute.CHANNEL_PREFERENCE, Attribute.PREFERRED_LANGUAGE}
    )
    assert switched.attributes == (Attribute.PREFERRED_LANGUAGE, Attribute.CHANNEL_PREFERENCE)


def test_the_subject_count_is_bounded() -> None:
    check_subject_count(routing(), 2)
    with pytest.raises(DomainError) as refused:
        check_subject_count(routing(), 1)
    assert refused.value.field == "subjects"


def test_the_catalogue_keys_are_written_out_for_every_question_and_value() -> None:
    from app.web.routing_labels import CATALOGUE_KEYS, question_key, value_key

    built = {question_key(SUBJECT_SOURCE)} | {
        key
        for attribute, codes in ATTRIBUTE_VALUES.items()
        for key in (question_key(attribute.value), *(value_key(attribute.value, c) for c in codes))
    }
    assert set(CATALOGUE_KEYS) == built


def refused_field(text: str | dict[str, str]) -> str | None:
    """Return the field the shared label check refuses ``text`` with, ``None`` when accepted."""
    try:
        if isinstance(text, str):
            validate_label(text, "the_field")
        else:
            validate_labels(text, "the_field")
    except DomainError as error:
        return error.field if error.code is ErrorCode.INVALID_CONFIGURATION else "other"
    return None


@pytest.mark.parametrize("text", ["", "   ", "x" * 81, "a\tb", "a\x7f"])
def test_a_blank_long_or_control_character_label_is_refused(text: str) -> None:
    assert refused_field(text) == "the_field"


def test_surrounding_spaces_do_not_count_towards_the_label_length() -> None:
    assert refused_field(" " + "x" * 80 + " ") is None
    assert refused_field("x" * 80) is None


def test_labels_need_both_required_locales_and_every_label_valid() -> None:
    assert refused_field({"nl": "a", "en": "b"}) is None
    assert refused_field({"nl": "a", "en": "b", "de": "c"}) is None
    assert refused_field({"nl": "a"}) == "the_field"
    assert refused_field({"nl": "a", "en": " "}) == "the_field"
    assert refused_field({"nl": "a", "en": "b", "de": "c" * 81}) == "the_field"
