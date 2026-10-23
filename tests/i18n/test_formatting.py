import pytest

from app.i18n.formatting import (
    MAX_TEXT_LENGTH,
    InvalidOverride,
    format_message,
    placeholders,
    validate_override,
)


def test_placeholders_lists_named_fields() -> None:
    assert placeholders("Hello {name}, slot {slot_start}") == frozenset({"name", "slot_start"})


def test_escaped_braces_are_not_placeholders() -> None:
    assert placeholders("Use {{literal}} braces") == frozenset()


def test_format_substitutes_named_fields() -> None:
    assert format_message("Hi {first_name}!", {"first_name": "Ada"}) == "Hi Ada!"


def test_format_unescapes_double_braces() -> None:
    assert format_message("{{x}} and {y}", {"y": 1}) == "{x} and 1"


def test_format_without_placeholders_ignores_params() -> None:
    assert format_message("Plain", {"unused": 1}) == "Plain"


def test_missing_parameter_raises_with_name_only() -> None:
    with pytest.raises(KeyError, match="first_name"):
        format_message("Hi {first_name}", {})


@pytest.mark.parametrize(
    "text", ["{x.__class__}", "{x[0]}", "{0}", "{x!r}", "{x:>10}", "{__import__}"]
)
def test_format_has_no_attribute_index_or_conversion_access(text: str) -> None:
    class Secret:
        def __repr__(self) -> str:
            return "leaked"

    rendered = format_message(text, {"x": Secret(), "__import__": "leaked"})

    assert "leaked" not in rendered
    assert placeholders(text) == frozenset()


def test_valid_override_passes() -> None:
    validate_override(source="Hello {name}", override="Hallo {name}!")


def test_override_must_keep_the_same_placeholders() -> None:
    with pytest.raises(InvalidOverride, match="placeholders"):
        validate_override(source="Hello {name}", override="Hallo {naam}")


def test_override_may_not_be_empty() -> None:
    with pytest.raises(InvalidOverride, match="empty"):
        validate_override(source="Hello", override="   ")


def test_override_of_exactly_the_maximum_length_passes() -> None:
    validate_override(source="x", override="x" * MAX_TEXT_LENGTH)


def test_override_has_a_length_limit() -> None:
    with pytest.raises(InvalidOverride, match="long"):
        validate_override(source="x", override="x" * (MAX_TEXT_LENGTH + 1))
