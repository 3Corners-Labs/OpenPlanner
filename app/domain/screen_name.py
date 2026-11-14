"""The staff member's screen name and what the participant is told of them (ADR 0123 point 10).

The participant of a call in our own page must see who they talk to, and the staff member must
not be findable by their account name. So the page shows, by default, a *screen name*: a name kept
with the staff member's record for the participant's side, with the organisation's own word for
staff next to it ("Sanne · medewerker"). Two pure rules live here:

    ``parse_screen_name``  what a screen name may be (normalised to NFC and trimmed;
                           ``MIN_LENGTH`` to ``MAX_LENGTH`` characters, counted after the
                           normalisation; letters of any script, digits, spaces, hyphen, apostrophe
                           and full stop, at least one letter or digit; a combining mark only after
                           a letter and at most ``MAX_MARKS`` in a row; empty clears it), with a
                           code for every refusal

Zero-width and bidirectional control characters are refused, and so is every other format
character, which also refuses the names that need a joiner to be spelled (some Persian and Indic
names): the label sits on a picture and in the captions, where such a character could hide or
reorder text. The manual says so.
    ``staff_label``        what the participant is told, given the organisation's setting and the
                           record of who accepted the work: a name or nothing, and the word

A screen name is the staff member's personal data. It is never logged, never in an audit detail
(the audit row says that it changed, not to what), never in a URL, and erasing the staff record
clears it (ADR 0046). The refusal codes are for the entry points to put into words; this module
holds no user-facing text.
"""

import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from app.domain.errors import DomainError, ErrorCode
from app.domain.guest_access import StaffNameMode

MIN_LENGTH = 2
MAX_LENGTH = 40
"""Bounds of a screen name in characters. Fixed: they follow from the label on the picture and
the length of one word of the staff term (ADR 0077), like ``agent_term.MAX_TERM_LENGTH``. The
column (``staff.screen_name``, migration 0118) and ``MAX_NAME`` of ``call-poll.js`` repeat the
upper bound; a test reads the three."""

MAX_MARKS = 2
"""Most combining marks in a row (an accent and one more: "ế"); a longer run is a stack that
draws over the lines around it."""

_PUNCTUATION = frozenset(" -'.’")
"""Besides letters, marks and digits, a screen name may hold a space, a hyphen, an apostrophe (also
the typographic one) and a full stop: "Jan-Willem", "O'Brien", "Sam K."."""


class ScreenNameRefusal(StrEnum):
    """Why a text is not a screen name; the entry points turn the code into words."""

    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"
    BAD_CHARACTER = "bad_character"
    NO_LETTER = "no_letter"
    BAD_MARKS = "bad_marks"


class ScreenNameError(DomainError):
    """A text that is not a screen name.

    Args:
        reason: Why; never the text itself, so no personal data travels in the error.
    """

    def __init__(self, reason: ScreenNameRefusal) -> None:
        """Store the reason; the field is always ``screen_name``."""
        super().__init__(ErrorCode.INVALID_CONFIGURATION, "screen_name")
        self.reason = reason


def _allowed(character: str) -> bool:
    """Return whether ``character`` may be in a screen name.

    A letter or mark of any script, a digit or one of the few marks of punctuation.
    """
    if character in _PUNCTUATION:
        return True
    category = unicodedata.category(character)
    return category[0] in ("L", "M") or category == "Nd"


def _marks_follow_letters(text: str) -> bool:
    """Return whether every combining mark follows a letter and no run is over ``MAX_MARKS``."""
    run = 0
    previous = ""
    for character in text:
        if unicodedata.category(character)[0] != "M":
            run, previous = 0, unicodedata.category(character)
            continue
        run += 1
        if run > MAX_MARKS or not (previous[:1] == "L" or previous[:1] == "M"):
            return False
        previous = unicodedata.category(character)
    return True


def parse_screen_name(text: str) -> str | None:
    """Return the screen name ``text`` stands for, or ``None`` when it clears the name.

    The text is normalised to NFC (so a decomposed and a composed spelling are one name and one
    length), trimmed, and its inner runs of white space become one space. An empty text is the
    request to have no screen name. Any other text is checked in this order: characters (no
    control, format or markup character), combining marks, a letter or digit somewhere, then
    length.

    Raises:
        ScreenNameError: With the code of the first rule the text breaks.
    """
    cleaned = " ".join(unicodedata.normalize("NFC", text).split())
    if not cleaned:
        return None
    if any(not _allowed(character) for character in cleaned):
        raise ScreenNameError(ScreenNameRefusal.BAD_CHARACTER)
    if not _marks_follow_letters(cleaned):
        raise ScreenNameError(ScreenNameRefusal.BAD_MARKS)
    if not any(
        unicodedata.category(c)[0] == "L" or unicodedata.category(c) == "Nd" for c in cleaned
    ):
        raise ScreenNameError(ScreenNameRefusal.NO_LETTER)
    if len(cleaned) < MIN_LENGTH:
        raise ScreenNameError(ScreenNameRefusal.TOO_SHORT)
    if len(cleaned) > MAX_LENGTH:
        raise ScreenNameError(ScreenNameRefusal.TOO_LONG)
    return cleaned


@dataclass(frozen=True)
class StaffLabel:
    """What the participant is told of the staff member, besides the organisation's word.

    Attributes:
        name: The name to show beside the word, or ``None`` when there is none to show. The word
            for staff is the page's own (it comes from the catalogue and the organisation's term),
            so it is not carried here.
    """

    name: str | None = None

    def as_json(self) -> dict[str, str]:
        """Return the answer of the page's read endpoint: ``{"name": ...}``, or ``{}`` for none."""
        return {"name": self.name} if self.name else {}


NO_LABEL = StaffLabel()
"""No name: the page shows the word for staff alone."""


def staff_label(
    mode: StaffNameMode, screen_name: str | None, account_name: str | None
) -> StaffLabel:
    """Return what the participant is told, by the organisation's choice.

    ``SCREEN_NAME`` is the screen name or nothing: the account's name is never a fallback, because
    the screen name exists so that the account's name is not shown. ``NAME`` is the account's own
    display name. ``TERM`` is the word alone. A staff member who accepted nothing yet, or whose
    record is gone or erased, is passed as ``None`` and has no name in any mode.

    Args:
        mode: ``embedded_call_staff_name`` of the organisation.
        screen_name: The screen name kept with the staff record, if any.
        account_name: The display name of the staff record (from the identity provider), if any.
    """
    chosen = {
        StaffNameMode.SCREEN_NAME: screen_name,
        StaffNameMode.NAME: account_name,
        StaffNameMode.TERM: None,
    }[mode]
    return StaffLabel(chosen.strip()[:MAX_LENGTH] or None) if chosen else NO_LABEL
