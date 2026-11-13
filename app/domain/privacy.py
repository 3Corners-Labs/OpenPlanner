"""The personal data of a booking: which fields, how sensitive, and where they may be shown.

ADR 0063 fixes this as a protection, so there is no setting and no way to change it at run
time: the list is a constant, and the rest of the code base imports it from here (the log
mask, the purge job, the marker-value template test, the *Data fields* card). A fixed rule
stays a constant on purpose (CLAUDE.md, ADR 0042).

Personal data (PII) lives only on ``Booking`` and, for a request without an appointment, on its
request-level record (``request_personal_data``, ADR 0121 decision D1); the record uses the
booking's field names (``REQUEST_DATA_FIELDS``), so this stays one list:

- *direct* fields identify the participant on their own: first and last name, email, phone;
- *sensitive* fields hold what the participant told us or give access to the conversation: the
  answers to the group's custom form fields (ADR 0055) and the join links (the call's own and
  the trial call's, ADR 0071).

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
    """Identifies the participant on its own: name, email address, phone number."""

    SENSITIVE = "sensitive"
    """What the participant told us, or a link that gives access to their conversation."""


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
    DataField("form_answers", Classification.SENSITIVE),
    DataField("guest_join_url", Classification.SENSITIVE),
    DataField("host_join_url", Classification.SENSITIVE),
    DataField("trial_join_url", Classification.SENSITIVE),
)
"""Every personal-data field, in the order the *Data fields* card shows them. The one list
of CLAUDE.md's privacy rules; a field that is not here is not personal data."""

PII_FIELDS: frozenset[str] = frozenset(f.name for f in DATA_FIELDS)
"""The names of ``DATA_FIELDS``, for membership tests."""

ALIASES: frozenset[str] = frozenset({"name", "answers", "note"})
"""Other names the same data travels by: ``name`` (one field before ADR 0055 split it),
``answers`` (the form answers on ``Contact``) and ``note`` (a column that no longer exists,
ADR 0102 point 13, kept so a log line that still uses the word is masked). The log mask covers
these too."""

DERIVED_FIELDS: tuple[str, ...] = (
    "video_room_id",
    "trial_room_id",
    "contact_center_task_id",
    "wxcc_task_id",
    "claimed_by",
)
"""Columns of ``Booking`` that are no personal data on their own but find it again: the
ids of the meeting, the trial meeting and the contact-center task (the key to a record at the
platform that holds the participant's name and email address) and the agent who claimed the
booking (ADR 0102 point 11). ``wxcc_task_id`` is the task id's column of before ADR 0032; it is
still written alongside ``contact_center_task_id`` until it is dropped a minor later (ADR 0116),
so the purge empties both. The purge empties them with the personal fields; they are not shown on
the *Data fields* card and not masked in logs."""

ROUTING_FIELDS: tuple[str, ...] = ("subject_code", "routing_answers")
"""The participant's subject and routing answers (ADR 0081): codes from a closed list, no
personal data, so not masked and not on the *Data fields* card; ADR 0081 has them purged with
the booking all the same. Reporting by subject (ADR 0117) must copy the code into its fact
before the purge; it cannot read it from a purged booking."""

PURGED_FIELDS: tuple[str, ...] = (
    *(f.name for f in DATA_FIELDS),
    *DERIVED_FIELDS,
    *ROUTING_FIELDS,
)
"""Every column the purge sets to NULL: ``DATA_FIELDS``, ``DERIVED_FIELDS`` and
``ROUTING_FIELDS``. The purge works from this list, so there is no second one to drift (ADR 0102
point 13). What the purge does is
*delete fields from the appointment*; it does not make the booking anonymous.

Deliberately not on it: ``contact_center_task_kind`` (ADR 0123 step 7a, ``form`` or
``conversation``) and ``contact_center_kind`` (the platform). Each is a word of a closed list
that finds nothing at the provider and says nothing about the participant, and the kind of a
task is kept so that a later step knows a conversation without asking the platform. The task's
*id* is what finds the record again, and that is purged."""

USAGE_FIELDS: tuple[str, ...] = (
    "kind",
    "bucket_hour",
    "organisation_id",
    "group_id",
    "booking_kind",
    "dispatch_kind",
    "subject_code",
    "locale",
    "channel_kind",
    "outcome",
    "actor_class",
    "reason_code",
    "value_seconds",
    "participants",
    "capacity",
    "booked",
    "staff_id",
)
"""The one allow-list of what a reporting fact may hold (ADR 0117 point 2): the fields of
``usage.UsageFact`` and, for the separate ``usage.StaffFact``, ``staff_id``. No field of
``DATA_FIELDS``, ``DERIVED_FIELDS`` or ``ALIASES`` is in it, and a test checks that and that the
fact types hold exactly these fields. The statistics hold no personal data; they are not a copy
of the appointments with the names taken out."""

ENTITY = "Booking"
"""The entity that holds the personal data of an appointment (CLAUDE.md privacy rules)."""

REQUEST_ENTITY = "ServiceRequest"
"""The entity whose request-level record holds the personal data of a request without an
appointment (ADR 0121 decision D1); a request with an appointment keeps it on ``Booking``."""

ENTITIES: tuple[str, ...] = (ENTITY, REQUEST_ENTITY)
"""Every entity that may hold personal data; nothing else does."""

REQUEST_DATA_FIELDS: tuple[str, ...] = ("first_name", "last_name", "email", "phone", "form_answers")
"""The columns of the request-level record (``request_personal_data``): a subset of
``DATA_FIELDS`` by name, so the log mask and the semgrep rule cover them; a test checks that and
that the table has exactly these personal columns. The deletion removes the record's row and
marks the request ``deleted`` (``service_requests.PersonalDataState``)."""

REQUEST_DERIVED_FIELDS: tuple[str, ...] = ("assigned_to",)
"""Columns of ``work_items`` that find the personal data again: the pseudonym of the agent who
took the work, like ``claimed_by`` on the booking. Emptied with the request's personal data: by
the booking's purge for an appointment, by the record's deletion without one."""

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
