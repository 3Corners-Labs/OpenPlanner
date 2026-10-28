"""The contact block as citizens read it, and its shared template partial (ADR 0037)."""

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.domain.contacts import ContactBlock, ContactKind, ContactOption
from app.i18n import Translator, load_catalogues
from app.web.contact_labels import contact_view
from app.web.locale import RequestLocale
from app.web.templates import SHARED_TEMPLATE_DIR

PHONE = ContactOption.create(
    ContactKind.PHONE, "14 020", hours={"nl": "ma-vr 9-17", "en": "Mon-Fri 9-5"}
)
CHAT = ContactOption.create(
    ContactKind.CHAT, "https://chat.gemeente.example/", labels={"nl": "Chat met ons"}
)
ADDRESS = ContactOption.create(ContactKind.ADDRESS, "Stadhuis\nAmstel 1")


@pytest.fixture(scope="module")
def translator() -> Translator:
    return Translator(load_catalogues())


def locale(translator: Translator, code: str) -> RequestLocale:
    return RequestLocale(translator.bind(code), remember=False, translator=translator)


def test_no_block_or_an_empty_block_shows_nothing(translator: Translator) -> None:
    assert contact_view(None, locale(translator, "nl")) is None
    assert contact_view(ContactBlock(()), locale(translator, "nl")) is None


def test_items_carry_term_link_lines_and_hours_in_the_readers_language(
    translator: Translator,
) -> None:
    view = contact_view(ContactBlock((PHONE, CHAT, ADDRESS)), locale(translator, "en"))

    assert view is not None
    assert view.heading == "Other ways to reach us"
    phone, chat, address = view.items
    assert (phone.term, phone.href, phone.text, phone.hours) == (
        "Phone",
        "tel:14020",
        "14 020",
        "Mon-Fri 9-5",
    )
    assert (chat.term, chat.href, chat.text) == (
        "Chat met ons",  # no English label: the Dutch one
        "https://chat.gemeente.example/",
        "chat.gemeente.example",
    )
    assert (address.term, address.href, address.lines) == (
        "Address",
        None,
        ("Stadhuis", "Amstel 1"),
    )


def test_the_admins_heading_replaces_the_default(translator: Translator) -> None:
    view = contact_view(ContactBlock((PHONE,), {"nl": "Bel ons"}), locale(translator, "nl"))

    assert view is not None
    assert view.heading == "Bel ons"


def test_the_partial_renders_links_and_addresses_escaped(translator: Translator) -> None:
    env = Environment(
        loader=FileSystemLoader(SHARED_TEMPLATE_DIR),
        autoescape=select_autoescape(default=True),
        undefined=StrictUndefined,
    )
    block = ContactBlock(
        (PHONE, ContactOption.create(ContactKind.ADDRESS, "<b>Stadhuis</b>\nAmstel 1")),
        {"nl": "Bel <ons>"},
    )
    view = contact_view(block, locale(translator, "nl"))
    html = env.from_string(
        '{% from "_contact_block.html" import contact_block %}{{ contact_block(c, level=3) }}'
    ).render(c=view)

    assert '<section class="contact-block" aria-labelledby="contacts-heading">' in html
    assert '<h3 id="contacts-heading" class="contact-block__heading">Bel &lt;ons&gt;</h3>' in html
    assert '<a class="contact-block__link" href="tel:14020">14 020</a>' in html
    assert "&lt;b&gt;Stadhuis&lt;/b&gt;<br>Amstel 1" in html
    assert '<span class="contact-block__hours">ma-vr 9-17</span>' in html
