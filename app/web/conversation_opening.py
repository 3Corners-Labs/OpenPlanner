"""The first message of a conversation task (ADR 0123 point 8, step 7a).

A conversation task opens with one text the staff member reads in their agent desktop. It is a
fixed text of the catalogue, in the organisation's language, and is filled with the fields
CLAUDE.md allows in an outbound contact-center payload and no others: the booking reference, the
first name, the start label, the group's name and the host join link. The names it may use are
``message_texts.CONVERSATION_OPENING_PLACEHOLDERS``; a test holds every catalogue text to them.
It is not an editable block: an administrator cannot add a field to it.
"""

from app.domain.calls import ContactCenterTask
from app.i18n import Translator

OPENING_WITH_LINK = "message.conversation.opening_video"
"""Catalogue key of the opening of a conversation that carries the meeting link."""
OPENING_WITHOUT_LINK = "message.conversation.opening"
"""Catalogue key of the opening of a conversation without a meeting (no link)."""


def conversation_opening(translator: Translator, locale: str, task: ContactCenterTask) -> str:
    """Return the first message of the conversation for ``task`` in ``locale``.

    The values are those of the neutral task, which holds nothing else of the booking.
    """
    key = OPENING_WITH_LINK if task.meeting_link else OPENING_WITHOUT_LINK
    return translator.gettext(
        locale,
        key,
        reference=task.reference,
        first_name=task.first_name,
        start=task.start_label,
        group_name=task.group_name,
        link=task.meeting_link,
    )
