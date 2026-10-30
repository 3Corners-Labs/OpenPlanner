"""The personal data of a booking: which fields, how sensitive, and where they may be shown.

ADR 0063 fixes this as a protection, so there is no setting and no way to change it at run
time: the list is a constant, and the rest of the code base imports it from here (the log
mask, the purge job, the marker-value template test, the *Data fields* card). A fixed rule
stays a constant on purpose (CLAUDE.md, ADR 0042).

Personal data (PII) lives only on ``Booking``:

- *direct* fields identify the citizen on their own: first and last name, email, phone;
- *sensitive* fields hold what the citizen told us or give access to the conversation: a
  note, the answers to the group's custom form fields (ADR 0055) and the join links (the
  call's own and the trial call's, ADR 0070).

Where it may appear: the booking detail page and the queue item, for a role holding a
``PII_PERMISSIONS`` permission (ADR 0039, 0063 §4), and the group's own agenda and queue show
the first name only (the same minimum that goes to the contact centre). Overview and list
pages, the audit log, exports, search results and alert texts never show any of it (§2), and
list filters never match on it (§3).
"""

from dataclasses import dataclass
from enum import StrEnum


class Classification(StrEnum):
    """How sensitive a personal-data field is (ADR 0063 §1)."""

    DIRECT = "direct"
    """Identifies the citizen on its own: name, email address, phone number."""

    SENSITIVE = "sensitive"
    """What the citizen told us, or a link that gives access to their conversation."""


@dataclass(frozen=True)
class DataField:
    """One personal-data field of ``Booking``.

    Attributes:
        name: The field as it is called on ``Booking`` and in the database column.
        classification: How sensitive it is.
    """

    name: str
    classification: Classification


DATA_FIELDS: tuple[DataField, ...] = (
    DataField("first_name", Classification.DIRECT),
    DataField("last_name", Classification.DIRECT),
    DataField("email", Classification.DIRECT),
    DataField("phone", Classification.DIRECT),
    DataField("note", Classification.SENSITIVE),
    DataField("form_answers", Classification.SENSITIVE),
    DataField("guest_join_url", Classification.SENSITIVE),
    DataField("host_join_url", Classification.SENSITIVE),
    DataField("trial_join_url", Classification.SENSITIVE),
)
"""Every personal-data field, in the order the *Data fields* card shows them. The one list
of CLAUDE.md's privacy rules; a field that is not here is not personal data."""

PII_FIELDS: frozenset[str] = frozenset(f.name for f in DATA_FIELDS)
"""The names of ``DATA_FIELDS``, for membership tests."""

ALIASES: frozenset[str] = frozenset({"name", "answers"})
"""Other names the same data travels by: ``name`` (one field before ADR 0055 split it) and
``answers`` (the form answers on ``Contact``). The log mask covers these too."""

ENTITY = "Booking"
"""The only entity that holds personal data (CLAUDE.md privacy rules)."""

_BY_NAME = {f.name: f for f in DATA_FIELDS}


def is_pii(field: str) -> bool:
    """Return whether ``field`` (a ``Booking`` field or column name) is personal data."""
    return field in PII_FIELDS


def classification(field: str) -> Classification:
    """Return how sensitive the personal-data field ``field`` is.

    Raises:
        ValueError: If ``field`` is not personal data; the caller asked the wrong question,
            so this is never answered with a default.
    """
    found = _BY_NAME.get(field)
    if found is None:
        raise ValueError(f"not a personal-data field: {field}")
    return found.classification


def fields_of(kind: Classification) -> tuple[str, ...]:
    """Return the names of the personal-data fields classified as ``kind``, in card order."""
    return tuple(f.name for f in DATA_FIELDS if f.classification is kind)
