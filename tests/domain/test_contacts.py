import pytest

from app.domain.contacts import (
    MAX_ADDRESS_LINES,
    MAX_HEADING_LENGTH,
    MAX_HOURS_LENGTH,
    MAX_LABEL_LENGTH,
    MAX_OPTIONS,
    ContactBlock,
    ContactKind,
    ContactOption,
    numbered_option,
)
from app.domain.errors import DomainError, ErrorCode


def refused_field(build: object) -> str | None:
    with pytest.raises(DomainError) as caught:
        build()  # type: ignore[operator]
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    return caught.value.field


def phone(value: str = "14 020", **kwargs: dict[str, str]) -> ContactOption:
    return ContactOption.create(ContactKind.PHONE, value, **kwargs)


# --- phone ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "href"),
    [
        ("14 020", "tel:14020"),
        ("020 - 624 1111", "tel:0206241111"),
        ("+31 (0)20 624 1111", "tel:+31206241111"),
        ("+31 20 624 1111", "tel:+31206241111"),
        ("112", "tel:112"),
    ],
)
def test_phone_numbers_link_with_digits_only(value: str, href: str) -> None:
    assert phone(value).href == href


def test_phone_number_white_space_is_collapsed() -> None:
    assert phone("  020   624  1111 ").value == "020 624 1111"


@pytest.mark.parametrize(
    "value",
    ["", "12", "020-CALL-NOW", "javascript:alert(1)", "+31 20 624 1111 1111 1111", "0"],
)
def test_phone_numbers_with_letters_or_odd_lengths_are_refused(value: str) -> None:
    assert refused_field(lambda: phone(value)) == "phone"


# --- email ------------------------------------------------------------------------------------


def test_email_links_with_mailto() -> None:
    option = ContactOption.create(ContactKind.EMAIL, " info@gemeente.example ")

    assert option.value == "info@gemeente.example"
    assert option.href == "mailto:info@gemeente.example"


@pytest.mark.parametrize(
    "value",
    [
        "info",
        "info@",
        "@gemeente.example",
        "info@localhost",
        "in fo@gemeente.example",
        "info@gemeente.example?subject=x",
        'a"b@gemeente.example',
        "info@-gemeente.example",
        "x" * 250 + "@gemeente.example",
    ],
)
def test_malformed_email_addresses_are_refused(value: str) -> None:
    assert refused_field(lambda: ContactOption.create(ContactKind.EMAIL, value)) == "email"


# --- website and chat -------------------------------------------------------------------------


@pytest.mark.parametrize("kind", [ContactKind.WEBSITE, ContactKind.CHAT])
def test_https_links_are_kept_as_given(kind: ContactKind) -> None:
    url = "https://www.gemeente.example/contact?onderwerp=paspoort#formulier"

    assert ContactOption.create(kind, url).href == url


@pytest.mark.parametrize(
    "url",
    [
        "http://www.gemeente.example",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "https://",
        "https://user:secret@www.gemeente.example",
        "https://www.gemeente.example/a b",
        'https://www.gemeente.example/"onmouseover',
        "//www.gemeente.example",
        "www.gemeente.example",
        "https://www.gemeente.example/" + "x" * 500,
    ],
)
@pytest.mark.parametrize("kind", [ContactKind.WEBSITE, ContactKind.CHAT])
def test_links_other_than_plain_https_are_refused(kind: ContactKind, url: str) -> None:
    assert refused_field(lambda: ContactOption.create(kind, url)) == "url"


def test_the_scheme_may_be_written_in_capitals() -> None:
    assert ContactOption.create(ContactKind.WEBSITE, "HTTPS://gemeente.example").href


# --- address ----------------------------------------------------------------------------------


def test_address_lines_are_trimmed_and_empty_lines_dropped() -> None:
    option = ContactOption.create(
        ContactKind.ADDRESS, "  Stadhuis \r\n\r\nAmstel 1\n1011 PN  Amsterdam  "
    )

    assert option.lines == ("Stadhuis", "Amstel 1", "1011 PN  Amsterdam")
    assert option.href is None


@pytest.mark.parametrize(
    "value",
    ["", " \n ", "\n".join(["regel"] * (MAX_ADDRESS_LINES + 1)), "x" * 81, "Amstel\x001"],
)
def test_empty_long_or_odd_addresses_are_refused(value: str) -> None:
    assert refused_field(lambda: ContactOption.create(ContactKind.ADDRESS, value)) == "address"


# --- labels and hours -------------------------------------------------------------------------


def test_labels_and_hours_fall_back_to_dutch() -> None:
    option = phone(
        labels={"nl": "Klantcontact", "en": ""}, hours={"nl": "ma-vr 9-17", "en": "Mon-Fri 9-5"}
    )

    assert option.label("en") == "Klantcontact"
    assert option.hours_text("en") == "Mon-Fri 9-5"
    assert option.hours_text("de") == "ma-vr 9-17"
    assert dict(option.labels) == {"nl": "Klantcontact"}


def test_an_option_without_label_or_hours_has_none() -> None:
    assert phone().label("nl") is None
    assert phone().hours_text("en") is None


@pytest.mark.parametrize(
    ("texts", "field"),
    [
        ({"labels": {"nl": "x" * (MAX_LABEL_LENGTH + 1)}}, "label"),
        ({"labels": {"en": "twee\nregels"}}, "label"),
        ({"hours": {"nl": "x" * (MAX_HOURS_LENGTH + 1)}}, "hours"),
        ({"hours": {"nl": "ma\x07vr"}}, "hours"),
        ({"labels": {"fr": "Contact"}}, "label"),
    ],
)
def test_long_multi_line_or_unknown_locale_texts_are_refused(
    texts: dict[str, dict[str, str]], field: str
) -> None:
    assert refused_field(lambda: phone(**texts)) == field


def test_an_unknown_kind_is_refused_with_its_number() -> None:
    assert refused_field(lambda: numbered_option(3, "fax", "020", {}, {})) == "contact_3_kind"


def test_numbered_options_name_the_row_in_the_refusal() -> None:
    assert (
        refused_field(lambda: numbered_option(2, "website", "http://x.example", {}, {}))
        == "contact_2_url"
    )


def test_numbered_options_build_the_option() -> None:
    option = numbered_option(1, "email", "info@gemeente.example", {"nl": "Mail"}, {})

    assert option == ContactOption.create(
        ContactKind.EMAIL, "info@gemeente.example", labels={"nl": "Mail"}
    )


def test_options_are_validated_also_when_built_directly() -> None:
    assert refused_field(lambda: ContactOption(ContactKind.WEBSITE, "javascript:x")) == "url"


# --- block ------------------------------------------------------------------------------------


def test_a_block_keeps_the_order_of_its_options() -> None:
    options = (phone(), ContactOption.create(ContactKind.EMAIL, "info@gemeente.example"))

    assert ContactBlock(options).options == options


def test_a_block_holds_at_most_six_options() -> None:
    assert refused_field(lambda: ContactBlock((phone(),) * (MAX_OPTIONS + 1))) == "contacts"


def test_a_block_without_options_is_empty() -> None:
    assert ContactBlock(()).is_empty
    assert not ContactBlock((phone(),)).is_empty


def test_headings_are_optional_and_fall_back_to_dutch() -> None:
    block = ContactBlock((phone(),), {"nl": "Bel ons", "en": " "})

    assert block.heading("en") == "Bel ons"
    assert dict(block.headings) == {"nl": "Bel ons"}
    assert ContactBlock((phone(),)).heading("nl") is None


@pytest.mark.parametrize(
    "headings", [{"nl": "x" * (MAX_HEADING_LENGTH + 1)}, {"en": "a\nb"}, {"de": "Kontakt"}]
)
def test_long_multi_line_or_unknown_locale_headings_are_refused(
    headings: dict[str, str],
) -> None:
    assert refused_field(lambda: ContactBlock((phone(),), headings)) == "contacts_heading"


def test_kinds_count_by_kind_for_the_audit() -> None:
    block = ContactBlock(
        (phone(), phone("112"), ContactOption.create(ContactKind.CHAT, "https://c.example"))
    )

    assert block.counts == "chat:1,phone:2"
