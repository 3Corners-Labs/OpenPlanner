"""The contact block as people read it, shared by participant and admin pages (ADR 0037 §2).

``contact_view`` (``block_view`` outside a request, for mails) turns a ``ContactBlock`` into
what ``_contact_block.html`` renders in the reader's language: a heading (the admin's, or a
catalogue text), and per option a term (its label, or the name of its kind), the link or the
address lines, and the opening hours.
"""

from dataclasses import dataclass

from fastapi import Request

from app.domain.contact_service import ContactService
from app.domain.contacts import ContactBlock, ContactKind, ContactOption
from app.i18n import BoundGettext
from app.web.locale import RequestLocale

KIND_LABELS: dict[ContactKind, str] = {
    ContactKind.PHONE: "contacts.kind.phone",
    ContactKind.EMAIL: "contacts.kind.email",
    ContactKind.WEBSITE: "contacts.kind.website",
    ContactKind.CHAT: "contacts.kind.chat",
    ContactKind.ADDRESS: "contacts.kind.address",
}
"""The name of each kind, shown when an option has no label (literal for the catalogue test)."""


@dataclass(frozen=True)
class ContactItemView:
    """One contact option ready for a template.

    Attributes:
        term: The option's label, or the name of its kind.
        href: Link target (``tel:``, ``mailto:``, ``https:``); ``None`` for an address.
        text: The link text: the number or address as typed, a URL without its scheme.
        lines: The address lines (empty for links).
        hours: Opening hours, if given.
    """

    term: str
    href: str | None
    text: str
    lines: tuple[str, ...]
    hours: str | None


@dataclass(frozen=True)
class ContactBlockView:
    """A contact block ready for ``_contact_block.html``."""

    heading: str
    items: list[ContactItemView]


def contact_service(request: Request) -> ContactService:
    """Return the application's contact service."""
    service: ContactService = request.app.state.contact_service
    return service


def kind_label(kind: ContactKind, _: BoundGettext) -> str:
    """Return the translated name of a contact kind (``Telefoon``)."""
    return _(KIND_LABELS[kind])


def _item(option: ContactOption, code: str, _: BoundGettext) -> ContactItemView:
    """Return one option in the reader's language."""
    term = option.label(code) or kind_label(option.kind, _)
    hours = option.hours_text(code)
    if option.kind is ContactKind.ADDRESS:
        return ContactItemView(term, None, "", option.lines, hours)
    text = option.value
    if option.kind in (ContactKind.WEBSITE, ContactKind.CHAT):
        text = option.value.split("://", 1)[1].rstrip("/")
    return ContactItemView(term, option.href, text, (), hours)


def block_view(block: ContactBlock | None, code: str, _: BoundGettext) -> ContactBlockView | None:
    """Return the block in locale ``code``, or ``None`` when there is nothing to show.

    Also used outside a request, for the closure mails (ADR 0037 §5).
    """
    if block is None or block.is_empty:
        return None
    heading = block.heading(code) or _("contacts.heading")
    return ContactBlockView(heading, [_item(o, code, _) for o in block.options])


def contact_view(block: ContactBlock | None, locale: RequestLocale) -> ContactBlockView | None:
    """Return the block for the reader's locale, or ``None`` when there is nothing to show."""
    return block_view(block, locale.code, locale.gettext)
