from datetime import date

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.form_fields import (
    Answers,
    Classification,
    FieldDefinition,
    FieldType,
    FormDefinition,
    Purpose,
    check_label,
    normalise_email,
    normalise_phone,
    raise_to_pii,
    validate_answers,
)

TODAY = date(2026, 10, 29)


def field(key: str = "customer_no", **overrides: object) -> FieldDefinition:
    values: dict[str, object] = {
        "key": key,
        "type": FieldType.SHORT_TEXT,
        "labels": {"nl": "Vraag", "en": "Question"},
    }
    values.update(overrides)
    options = values.get("options", ())
    if "option_labels" not in values and isinstance(options, tuple):
        values["option_labels"] = {o: {"nl": o, "en": o} for o in options}
    return FieldDefinition.create(**values)  # type: ignore[arg-type]


def form(*fields: FieldDefinition) -> FormDefinition:
    return FormDefinition.create(version=3, fields=fields, max_custom_fields=5)


def answer(definition: FieldDefinition, raw: str | None) -> object:
    answers = validate_answers(form(definition), {definition.key: raw}, today=TODAY, region="NL")
    return answers.visible(Purpose.PLANNER_VIEW).get(definition.key)


def refused(definition: FieldDefinition, raw: str | None) -> DomainError:
    with pytest.raises(DomainError) as caught:
        validate_answers(form(definition), {definition.key: raw}, today=TODAY, region="NL")
    assert caught.value.code is ErrorCode.INVALID_ANSWER
    return caught.value


# --- definitions ------------------------------------------------------------------------


def test_a_new_field_is_pii_and_optional_by_default() -> None:
    f = field()

    assert f.classification is Classification.PII
    assert f.required is False


@pytest.mark.parametrize("key", ["", "1abc", "Customer", "a", "with space", "x" * 41, "a-b"])
def test_keys_are_lowercase_identifiers(key: str) -> None:
    with pytest.raises(DomainError) as caught:
        field(key)
    assert caught.value.code is ErrorCode.INVALID_FORM_FIELD


@pytest.mark.parametrize("key", ["first_name", "last_name", "email", "phone"])
def test_system_keys_cannot_be_reused(key: str) -> None:
    with pytest.raises(DomainError):
        field(key)


@pytest.mark.parametrize("kind", [FieldType.EMAIL, FieldType.PHONE, FieldType.LONG_TEXT])
def test_contact_details_and_free_text_are_always_pii(kind: FieldType) -> None:
    with pytest.raises(DomainError):
        field("other_contact", type=kind, classification=Classification.NOT_PII)


@pytest.mark.parametrize("key", ["bsn", "burgerservicenummer", "medische_reden", "religion"])
def test_forbidden_terms_are_refused_in_keys(key: str) -> None:
    with pytest.raises(DomainError) as caught:
        field(key)
    assert caught.value.code is ErrorCode.FORM_FIELD_FORBIDDEN


@pytest.mark.parametrize(
    "label", ["Uw BSN", "Burger Service Nummer", "Paspoortnummer", "Diagnose", "Geloof"]
)
def test_forbidden_terms_are_refused_in_labels(label: str) -> None:
    with pytest.raises(DomainError) as caught:
        check_label(label)
    assert caught.value.code is ErrorCode.FORM_FIELD_FORBIDDEN


def test_an_ordinary_label_passes() -> None:
    check_label("Klantnummer")


def test_choice_needs_unique_option_keys() -> None:
    with pytest.raises(DomainError):
        field("topic", type=FieldType.CHOICE)
    with pytest.raises(DomainError):
        field("topic", type=FieldType.CHOICE, options=("a_b", "a_b"))
    with pytest.raises(DomainError):
        field("topic", type=FieldType.CHOICE, options=tuple(f"o{i}" for i in range(31)))
    with pytest.raises(DomainError):
        field("topic", type=FieldType.CHOICE, options=("Not A Key",))


def test_options_and_bounds_only_where_they_mean_something() -> None:
    with pytest.raises(DomainError):
        field(options=("a1",))
    with pytest.raises(DomainError):
        field(minimum=1)
    with pytest.raises(DomainError):
        field("count", type=FieldType.NUMBER, minimum=5, maximum=1)


def test_form_limits_custom_fields_and_duplicate_keys() -> None:
    with pytest.raises(DomainError) as caught:
        FormDefinition.create(
            version=1,
            fields=tuple(field(f"field_{i}") for i in range(3)),
            max_custom_fields=2,
        )
    assert caught.value.code is ErrorCode.INVALID_FORM_FIELD
    with pytest.raises(DomainError):
        form(field("dup"), field("dup"))


def test_retired_keys_cannot_be_used_again_and_do_not_count() -> None:
    old = field("old_question")
    current = FormDefinition.create(
        version=2, fields=(field("a1"), field("a2")), max_custom_fields=2, retired=(old,)
    )

    assert current.get("old_question") is None
    assert current.find("old_question") is not None
    assert current.find("old_question").retired  # type: ignore[union-attr]
    with pytest.raises(DomainError):
        FormDefinition.create(
            version=2, fields=(field("old_question"),), max_custom_fields=2, retired=(old,)
        )


@pytest.mark.parametrize("labels", [{"nl": "Vraag"}, {"en": "Question"}, {"nl": " ", "en": "Q"}])
def test_labels_need_dutch_and_english(labels: dict[str, str]) -> None:
    with pytest.raises(DomainError) as caught:
        field(labels=labels)
    assert str(caught.value) == "invalid_form_field: label"


def test_labels_are_cleaned_and_bounded() -> None:
    f = field(labels={"nl": "  Klant  nummer ", "en": "Customer number", "de": ""})

    assert f.labels == {"nl": "Klant nummer", "en": "Customer number"}
    with pytest.raises(DomainError):
        field(labels={"nl": "x" * 201, "en": "x"})
    with pytest.raises(DomainError):
        field(labels={"nl": "a\x00b", "en": "x"})
    with pytest.raises(DomainError):
        field(labels={"nl": "Uw BSN", "en": "Number"})


def test_texts_fall_back_to_dutch_then_the_key() -> None:
    f = field(
        "topic",
        type=FieldType.CHOICE,
        options=("rent",),
        hints={"nl": "Kies er een"},
        option_labels={"rent": {"nl": "Huur", "en": "Rent"}},
    )

    assert f.label("en") == "Question"
    assert f.label("de") == "Vraag"
    assert f.hint("en") == "Kies er een"
    assert f.option_label("rent", "en") == "Rent"
    assert f.option_label("other", "en") == "other"
    assert field(hints={}).hint("nl") == ""


def test_option_labels_belong_to_options_and_need_both_locales() -> None:
    with pytest.raises(DomainError):
        field("topic", type=FieldType.CHOICE, options=("rent",), option_labels={"x": {}})
    with pytest.raises(DomainError):
        field(
            "topic", type=FieldType.CHOICE, options=("rent",), option_labels={"rent": {"nl": "H"}}
        )


# --- answers per type ------------------------------------------------------------------


def test_short_text_is_trimmed_and_bounded() -> None:
    f = field()

    assert answer(f, "  AB   12 ") == "AB 12"
    refused(f, "x" * 101)
    refused(f, "a\x00b")


def test_long_text_keeps_line_breaks() -> None:
    f = field("remark", type=FieldType.LONG_TEXT)
    answers = validate_answers(form(f), {"remark": " line 1\r\nline 2 "}, today=TODAY, region="NL")

    assert answers.visible(Purpose.PLANNER_VIEW)["remark"] == "line 1\nline 2"


def test_email_answer() -> None:
    f = field("other_email", type=FieldType.EMAIL)

    assert answer(f, " ada@example.org ") == "ada@example.org"
    refused(f, "ada@example")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("06 12345678", "+31612345678"),
        ("0031 20 123 4567", "+31201234567"),
        ("+32 470 12 34 56", "+32470123456"),
    ],
)
def test_phone_answer_is_e164(raw: str, expected: str) -> None:
    assert answer(field("mobile", type=FieldType.PHONE), raw) == expected


@pytest.mark.parametrize("raw", ["12345", "06 1234", "not a number", "+999 1234567"])
def test_invalid_phone_numbers_are_refused(raw: str) -> None:
    refused(field("mobile", type=FieldType.PHONE), raw)


def test_number_answer_respects_bounds() -> None:
    f = field("people", type=FieldType.NUMBER, minimum=1, maximum=10)

    assert answer(f, " 4 ") == 4
    for raw in ("0", "11", "2.5", "four", "1e3"):
        refused(f, raw)


def test_date_answer_respects_relative_bounds() -> None:
    f = field("moved_on", type=FieldType.DATE, minimum=-30, maximum=0)

    assert answer(f, "2026-10-01") == "2026-10-01"
    refused(f, "2026-09-28")
    refused(f, "2026-10-30")
    refused(f, "29-10-2026")
    refused(f, "2026-02-30")


@pytest.mark.parametrize(("raw", "expected"), [("1234ab", "1234 AB"), (" 1234 AB ", "1234 AB")])
def test_postcode_is_normalised(raw: str, expected: str) -> None:
    assert answer(field("postcode", type=FieldType.POSTCODE_NL), raw) == expected


@pytest.mark.parametrize("raw", ["0123 AB", "1234 SS", "12345", "1234 A"])
def test_invalid_postcodes_are_refused(raw: str) -> None:
    refused(field("postcode", type=FieldType.POSTCODE_NL), raw)


def test_choice_accepts_only_its_options() -> None:
    f = field("topic", type=FieldType.CHOICE, options=("rent", "benefits"))

    assert answer(f, "rent") == "rent"
    refused(f, "something else")


def test_checkbox() -> None:
    optional = field("newsletter", type=FieldType.CHECKBOX)
    consent = field("consent", type=FieldType.CHECKBOX, required=True)

    assert answer(optional, None) is False
    assert answer(optional, "on") is True
    refused(consent, None)
    refused(consent, "maybe")


# --- required, unknown and error hygiene -------------------------------------------------


def test_required_field_must_be_answered_and_optional_may_be_empty() -> None:
    assert answer(field(), "  ") is None
    error = refused(field(required=True), " ")
    assert str(error) == "invalid_answer: customer_no"


def test_unknown_keys_are_refused_without_echoing_the_key() -> None:
    with pytest.raises(DomainError) as caught:
        validate_answers(form(field()), {"j.doe@example.org": "x"}, today=TODAY, region="NL")

    assert str(caught.value) == "invalid_answer: unknown"


def test_errors_never_contain_the_value() -> None:
    error = refused(field(), "geheim\x00")

    assert "geheim" not in str(error)


# --- the one enforcement point ----------------------------------------------------------


def _mixed_answers() -> Answers:
    secret = field("customer_no", required=True)
    public = field(
        "topic", type=FieldType.CHOICE, options=("rent",), classification=Classification.NOT_PII
    )
    return validate_answers(
        form(secret, public), {"customer_no": "A-1", "topic": "rent"}, today=TODAY, region="NL"
    )


def test_the_agenda_booking_page_sees_everything() -> None:
    assert _mixed_answers().visible(Purpose.PLANNER_VIEW) == {"customer_no": "A-1", "topic": "rent"}


@pytest.mark.parametrize(
    "purpose",
    [Purpose.AGENT_VIEW, Purpose.EXPORT, Purpose.MESSAGE_PLACEHOLDER, Purpose.AUDIT, Purpose.LOG],
)
def test_other_purposes_see_only_non_pii(purpose: Purpose) -> None:
    assert _mixed_answers().visible(purpose) == {"topic": "rent"}


def test_dispatch_sees_nothing() -> None:
    assert _mixed_answers().visible(Purpose.DISPATCH) == {}


def test_every_purpose_has_a_rule() -> None:
    answers = _mixed_answers()
    for purpose in Purpose:
        answers.visible(purpose)


def test_answers_never_show_values_in_repr() -> None:
    assert "A-1" not in repr(_mixed_answers())


def test_storage_round_trip_keeps_version_and_classes() -> None:
    stored = _mixed_answers().to_storage()
    restored = Answers.from_storage(stored)

    assert stored["version"] == 3
    assert restored.visible(Purpose.LOG) == {"topic": "rent"}
    assert restored.visible(Purpose.PLANNER_VIEW) == {"customer_no": "A-1", "topic": "rent"}


def test_raising_a_tag_makes_stored_answers_pii() -> None:
    stored = raise_to_pii(_mixed_answers().to_storage(), "topic")

    assert Answers.from_storage(stored).visible(Purpose.LOG) == {}


def test_stored_garbage_counts_as_pii_and_odd_values_are_dropped() -> None:
    restored = Answers.from_storage(
        {"answers": {"a": "x", "b": ["list"]}, "not_pii": ["a", "ghost"]}
    )

    assert restored.version == 0
    assert restored.visible(Purpose.PLANNER_VIEW) == {"a": "x"}
    assert restored.visible(Purpose.LOG) == {"a": "x"}


# --- shared normalisers -----------------------------------------------------------------


def test_normalisers_name_the_field_they_were_given() -> None:
    with pytest.raises(DomainError) as caught:
        normalise_email("nope", "email")
    assert str(caught.value) == "invalid_contact: email"
    with pytest.raises(DomainError):
        normalise_phone("123", "NL", "phone")
