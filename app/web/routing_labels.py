"""The i18n keys of the routing questions and catalogue values (ADR 0081).

Shared by the participant's contact form (``app.api``) and the admin routing screen
(``app.admin``), which may not import each other.
"""

from app.domain.routing import SUBJECT_SOURCE, GroupRouting, RoutingChoice
from app.i18n import BoundGettext

CATALOGUE_KEYS = (
    "booking.routing.subject.label",
    "booking.routing.preferred_language.label",
    "booking.routing.preferred_language.value.nl",
    "booking.routing.preferred_language.value.en",
    "booking.routing.channel_preference.label",
    "booking.routing.channel_preference.value.video",
    "booking.routing.channel_preference.value.phone",
)
"""The keys ``question_key`` and ``value_key`` build, written out so the catalogue check
finds them; a test keeps this list equal to the attribute catalogue."""


def question_key(source: str) -> str:
    """Return the i18n key of a routing question's label (``subject`` or an attribute)."""
    return f"booking.routing.{source}.label"


def value_key(source: str, code: str) -> str:
    """Return the i18n key of a catalogue attribute value's label (ADR 0081 point 3)."""
    return f"booking.routing.{source}.value.{code}"


def source_label(source: str, gettext: BoundGettext) -> str:
    """Return the question of a routing source (``subject`` or an attribute) in words."""
    return gettext(question_key(source))


def mapping_source_label(source: str, gettext: BoundGettext) -> str:
    """Return a mapping source as the admin screen names it: *Subject*, or the question."""
    if source == SUBJECT_SOURCE:
        return gettext("admin.routing.source.subject")
    return source_label(source, gettext)


def option_label(
    routing: GroupRouting, source: str, code: str, gettext: BoundGettext, locale: str
) -> str:
    """Return the label of one code: the group's subject label, or the catalogue value's.

    A subject code no longer in the group's list shows the code itself.
    """
    if source == SUBJECT_SOURCE:
        subject = routing.subject(code)
        return subject.label(locale) if subject else code
    return gettext(value_key(source, code))


def routing_summary(
    routing: GroupRouting, choice: RoutingChoice | None, gettext: BoundGettext, locale: str
) -> str:
    """Return the participant's subject and attributes as one line for staff (ADR 0081).

    ``Billing · Preferred language: English``. A subject no longer in the group's list shows its
    code; an empty string without a choice.
    """
    if choice is None:
        return ""
    parts: list[str] = []
    if choice.subject is not None:
        parts.append(option_label(routing, SUBJECT_SOURCE, choice.subject, gettext, locale))
    for source, code in choice.attributes:
        answer = option_label(routing, source, code, gettext, locale)
        parts.append(f"{source_label(source, gettext)}: {answer}")
    return " · ".join(parts)
