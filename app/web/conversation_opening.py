"""The first message of a conversation task (ADR 0123 point 8, step 7a).

A conversation task opens with one text the staff member reads in their agent desktop. It is a
fixed text of the catalogue, in the organisation's language, and is filled with the fields
CLAUDE.md allows in an outbound contact-center payload and no others: the booking reference, the
first name, the start label, the group's name and the host join link. The names it may use are
``message_texts.CONVERSATION_OPENING_PLACEHOLDERS``; a test holds every catalogue text to them.
It is not an editable block: an administrator cannot add a field to it.
"""

from app.domain.calls import ContactCenterTask, OpeningTextError
from app.domain.message_texts import CONVERSATION_OPENING_PLACEHOLDERS, used_placeholders
from app.i18n import DEFAULT_LOCALE, Translator

OPENING_WITH_LINK = "message.conversation.opening_video"
"""Catalogue key of the opening of a conversation that carries the meeting link."""
OPENING_WITHOUT_LINK = "message.conversation.opening"
"""Catalogue key of the opening of a conversation without a meeting (no link)."""


def conversation_opening(
    translator: Translator, locale: str | None, task: ContactCenterTask
) -> str:
    """Return the first message of the conversation for ``task``.

    ``locale`` is the organisation's default language; ``None`` is the installation's default.
    The values are those of the neutral task, which holds nothing else of the booking.

    Raises:
        OpeningTextError: If the catalogue text of the language uses a placeholder outside
            ``CONVERSATION_OPENING_PLACEHOLDERS`` or is malformed: a mistake in the catalogue,
            found by a test, never worked around at run time (the text is fixed, not an
            administrator's). The dispatch refuses it as not ready.
    """
    language = locale or DEFAULT_LOCALE
    key = OPENING_WITH_LINK if task.meeting_link else OPENING_WITHOUT_LINK
    names = used_placeholders(translator.lookup(language, key))
    if names is None or not names <= CONVERSATION_OPENING_PLACEHOLDERS:
        raise OpeningTextError(f"the text {key!r} uses a placeholder the first message may not")
    return translator.gettext(
        language,
        key,
        reference=task.reference,
        first_name=task.first_name,
        start=task.start_label,
        group_name=task.group_name,
        link=task.meeting_link,
    )
