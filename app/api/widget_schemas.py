"""Request and response bodies of the widget API (ADR 0035 §2), part of ``/api/v1``.

Responses carry what the standalone pages show, already translated for the requested locale,
so the Web Component renders without its own date or text logic and both shells say the same.
No response holds personal data or a management token; errors are codes, never text.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_ANSWERS = 15
"""At most as many answers as the platform's highest field limit (ADR 0055)."""
MAX_KEY_LENGTH = 40
MAX_ANSWER_LENGTH = 1001
"""One more than a long text allows, so the domain refuses it rather than this cut it short."""


class WidgetError(BaseModel):
    """A refusal: a stable code and, for a contact input, the field it concerns."""

    error: str = Field(
        description="`group_not_found`, `origin_not_allowed`, `invalid_request`, "
        "`invalid_contact`, `slot_unavailable`, `slot_full`, "
        "`outside_booking_window`, `group_closed`, `rate_limited`, …"
    )
    field: str | None = Field(
        default=None,
        description="`first_name`, `last_name`, `email`, `phone`, or `q_<key>` for a custom "
        "field (`invalid_answer`).",
    )


class WidgetTexts(BaseModel):
    """The texts the widget shows before it knows the group: loading, unavailable, retry."""

    locale: str
    texts: dict[str, str]


class WidgetOption(BaseModel):
    """One choice of a radio group."""

    value: str
    label: str


class WidgetField(BaseModel):
    """One input of the contact form.

    First and last name, email, per group phone, and the group's custom fields (``q_<key>``,
    ADR 0055).
    """

    name: str
    type: str
    autocomplete: str
    label: str
    required: bool
    hint: str | None = None
    kind: Literal["input", "textarea", "radio", "checkbox"] = "input"
    options: list[WidgetOption] = []
    min: str | None = None
    max: str | None = None
    inputmode: str | None = None


class WidgetClosure(BaseModel):
    """An emergency closure citizens must see (ADR 0024/0037); no booking while active."""

    title: str
    period: str
    heading: str | None = None
    paragraphs: list[str]
    active: bool


class WidgetGroup(BaseModel):
    """The group, its form and the texts of the widget in one locale."""

    name: str
    locale: str
    intro: str
    page_url: str = Field(description="The standalone page, for the direct call and filters.")
    closure: WidgetClosure | None = None
    fields: list[WidgetField]
    honeypot: str = Field(description="Name of the field that must stay empty.")
    texts: dict[str, str]


class WidgetDay(BaseModel):
    """One day of the month grid."""

    date: str = Field(description="`YYYY-MM-DD`")
    number: int
    label: str
    free: bool
    today: bool
    later: bool = Field(description="Beyond the booking horizon, not closed or full.")


class WidgetWeekday(BaseModel):
    """A column heading of the month grid."""

    short: str
    name: str


class WidgetMonth(BaseModel):
    """The month grid, Monday first; ``null`` pads the days before the 1st and after the end."""

    month: str = Field(description="`YYYY-MM`")
    heading: str
    previous: str | None = Field(description="`YYYY-MM` of the previous month, if bookable.")
    next: str | None = Field(description="`YYYY-MM` of the next month, if bookable.")
    weekdays: list[WidgetWeekday]
    weeks: list[list[WidgetDay | None]]
    horizon: str = Field(description="How far ahead citizens can book, as a sentence.")
    has_later: bool


class WidgetTime(BaseModel):
    """One free time; ``start`` is the value to book."""

    start: str = Field(description="UTC, ISO 8601 with `Z`.")
    label: str


class WidgetTimeGroup(BaseModel):
    """The free times of one part of the day."""

    heading: str
    times: list[WidgetTime]


class WidgetTimes(BaseModel):
    """The free times of the selected day."""

    date: str = Field(description="`YYYY-MM-DD`")
    heading: str
    count: str
    groups: list[WidgetTimeGroup]


class WidgetAvailability(BaseModel):
    """The month and the times of one day, as the standalone page shows them."""

    month: WidgetMonth
    times: WidgetTimes | None
    first: WidgetTime | None = Field(description="The first free moment, if any.")


class WidgetBookingRequest(BaseModel):
    """What the citizen enters; ``website`` is the honeypot and must stay empty."""

    model_config = ConfigDict(extra="forbid")

    start: str = Field(max_length=40, description="A `start` from the availability.")
    first_name: str | None = Field(default=None, max_length=300)
    last_name: str | None = Field(default=None, max_length=300)
    name: str | None = Field(
        default=None,
        max_length=300,
        description="Deprecated: the full name, split at the first space when `first_name` "
        "is missing (ADR 0055).",
    )
    email: str = Field(max_length=300)
    phone: str | None = Field(default=None, max_length=300)
    answers: dict[str, str] = Field(
        default_factory=dict,
        max_length=MAX_ANSWERS,
        description="Answers to the group's custom fields, by key (without `q_`).",
    )
    website: str | None = Field(default=None, max_length=300)

    @field_validator("answers")
    @classmethod
    def _bounded_answers(cls, value: dict[str, str]) -> dict[str, str]:
        """Refuse keys and values longer than any field allows."""
        if any(len(k) > MAX_KEY_LENGTH or len(v) > MAX_ANSWER_LENGTH for k, v in value.items()):
            raise ValueError("answer too long")
        return value

    def names(self) -> tuple[str, str]:
        """Return first and last name, from the two fields or the deprecated ``name``."""
        if self.first_name is not None or self.name is None:
            return self.first_name or "", self.last_name or ""
        first, _, last = self.name.strip().partition(" ")
        return first, last


class WidgetBooking(BaseModel):
    """The booking as confirmed; the management link goes to the citizen by email only."""

    status: Literal["booked"]
    start: str
    end: str
    label: str
    message: str
