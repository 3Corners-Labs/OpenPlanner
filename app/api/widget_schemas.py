"""Request and response bodies of the widget API (ADR 0035 §2), part of ``/api/v1``.

Responses carry what the standalone pages show, already translated for the requested locale,
so the Web Component renders without its own date or text logic and both shells say the same.
No response holds personal data or a management token; errors are codes, never text.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WidgetError(BaseModel):
    """A refusal: a stable code and, for a contact input, the field it concerns."""

    error: str = Field(
        description="`group_not_found`, `origin_not_allowed`, `invalid_request`, "
        "`invalid_contact`, `slot_unavailable`, `slot_full`, "
        "`outside_booking_window`, `group_closed`, `rate_limited`, …"
    )
    field: str | None = Field(default=None, description="`name`, `email` or `phone`.")


class WidgetTexts(BaseModel):
    """The texts the widget shows before it knows the group: loading, unavailable, retry."""

    locale: str
    texts: dict[str, str]


class WidgetField(BaseModel):
    """One input of the contact form: name, email and, per group, phone."""

    name: str
    type: str
    autocomplete: str
    label: str
    required: bool
    hint: str | None = None


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
    name: str = Field(max_length=300)
    email: str = Field(max_length=300)
    phone: str | None = Field(default=None, max_length=300)
    website: str | None = Field(default=None, max_length=300)


class WidgetBooking(BaseModel):
    """The booking as made; it awaits confirmation by the link in the citizen's email."""

    status: Literal["booked"]
    start: str
    end: str
    label: str
    message: str
