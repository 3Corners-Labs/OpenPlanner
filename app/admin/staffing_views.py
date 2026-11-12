"""The weekly blocks and exceptions of a staffing schedule, as screens show and edit them.

One home for the view models and form inputs of a week of staffing blocks and its per-date
exceptions. A service's *Staffing* tab (``app.admin.group_routes``, ADR 0027) and a counted
capacity pool's staffing pages (``app.admin.pool_staffing_routes``, ADR 0122) both build their
screens from it, so a block reads and is checked the same wherever it is entered. What differs
stays with the caller: the subjects a service's block may offer (passed as ``subjects``), the
paths, the permission and the use case that stores the result.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.admin.forms import Field, FormData, Option
from app.domain.errors import DomainError
from app.domain.scheduling import (
    MAX_LABEL_LENGTH,
    Offer,
    ScheduleOverride,
    ScheduleRule,
    TimeWindow,
)
from app.i18n import BoundGettext
from app.i18n.dates import format_day, format_day_short, format_time_of_day, weekday_names
from app.web.locale import RequestLocale

WEEKDAYS = frozenset(str(i) for i in range(7))
"""The values of the weekday select: 0 (Monday) to 6."""

OFFER_LABELS: dict[Offer, str] = {
    Offer.SCHEDULED: "admin.offer.scheduled",
    Offer.DIRECT: "admin.offer.direct",
    Offer.BOTH: "admin.offer.both",
}
"""Catalogue key per offer of a block (ADR 0072), for the select and the week view."""

OFFER_VALUES = frozenset(offer.value for offer in Offer)


@dataclass(frozen=True)
class Hints:
    """The catalogue keys of the two hints that differ between a service and a pool."""

    capacity: str
    offer: str


SERVICE_HINTS = Hints("admin.field.capacity_hint", "admin.field.offer_hint")
"""A service's block: that many participants can book one time slot."""

POOL_HINTS = Hints("admin.pool_staffing.capacity_hint", "admin.pool_staffing.offer_hint")
"""A pool's block: that many people are counted at the same time."""

SubjectReader = Callable[[], tuple[str, ...]]
"""Reads the subjects ticked on a service's form; a pool's block has none."""


@dataclass(frozen=True)
class DayView:
    """One weekday (0 = Monday) with its staffing blocks."""

    weekday: int
    name: str
    windows: list["WindowView"]


@dataclass(frozen=True)
class WindowView:
    """A staffing block as shown in the week: times, agents in words, label, validity.

    ``staffed`` is false for a 0-agent block; the template marks it with a class, and the
    agent text ("geen medewerkers") says the same in words, so colour is never the only signal.
    """

    id: int | None
    time: str
    capacity: str
    validity: str | None
    label: str | None
    staffed: bool
    offer: str | None = None
    """The block's offer in words when it is not the default (ADR 0072)."""
    version: str = ""
    """What a pool's change and removal forms send back (``expected``); empty on a service's."""


@dataclass(frozen=True)
class ExceptionView:
    """An exception as shown in the list."""

    id: int | None
    day: str
    closed: bool
    time: str | None
    capacity: str | None
    offer: str | None = None
    version: str = ""
    """What the removal form sends back (``expected``); empty where nothing checks it."""


def clock_text(window: TimeWindow, locale: str) -> str:
    """Return ``09:00–12:00`` in the system clock style."""
    return f"{format_time_of_day(window.opens, locale)}–{format_time_of_day(window.closes, locale)}"


def agents_text(count: int, _: BoundGettext) -> str:
    """Return "3 medewerkers", "1 medewerker" or "geen medewerkers" (no plural forms yet)."""
    if count == 0:
        return _("admin.hours.capacity_none")
    if count == 1:
        return _("admin.hours.capacity_one")
    return _("admin.hours.capacity", count=count)


def offer_text(window: TimeWindow, _: BoundGettext) -> str | None:
    """Return the block's offer in words, or ``None`` for the default (slots only)."""
    return None if window.offer is Offer.SCHEDULED else _(OFFER_LABELS[window.offer])


def week(
    rules: Sequence[ScheduleRule],
    locale: RequestLocale,
    version: Callable[[ScheduleRule], str] | None = None,
) -> list[DayView]:
    """Group the weekly ``rules`` per weekday, Monday first, in the order given."""
    _ = locale.gettext
    days = []
    for weekday, name in enumerate(weekday_names(locale.code)):
        windows = []
        for rule in (r for r in rules if r.weekday == weekday):
            validity = None
            if rule.valid_from or rule.valid_to:
                validity = _(
                    "admin.hours.validity",
                    start=format_day_short(rule.valid_from, locale.code)
                    if rule.valid_from
                    else "…",
                    end=format_day_short(rule.valid_to, locale.code) if rule.valid_to else "…",
                )
            windows.append(
                WindowView(
                    rule.id,
                    clock_text(rule.window, locale.code),
                    agents_text(rule.window.capacity, _),
                    validity,
                    rule.window.label,
                    rule.window.staffed,
                    offer_text(rule.window, _),
                    version(rule) if version else "",
                )
            )
        days.append(DayView(weekday, name, windows))
    return days


def exception_views(
    overrides: Sequence[ScheduleOverride],
    locale: RequestLocale,
    version: Callable[[ScheduleOverride], str] | None = None,
) -> list[ExceptionView]:
    """Return the exceptions for display, in the order given."""
    _ = locale.gettext
    return [
        ExceptionView(
            o.id,
            format_day(o.day, locale.code),
            o.window is None,
            clock_text(o.window, locale.code) if o.window else None,
            agents_text(o.window.capacity, _) if o.window else None,
            offer_text(o.window, _) if o.window else None,
            version(o) if version else "",
        )
        for o in overrides
    ]


def block_description(rule: ScheduleRule, locale: RequestLocale) -> str:
    """Return a block in words: weekday, times, agents and label, for the split form's lead."""
    _ = locale.gettext
    day = weekday_names(locale.code)[rule.weekday]
    parts = [
        f"{day} {clock_text(rule.window, locale.code)}",
        agents_text(rule.window.capacity, _),
    ]
    return " · ".join([*parts, rule.window.label] if rule.window.label else parts)


def label_field(form: FormData, locale: RequestLocale) -> Field:
    """Return the optional label input of a block (ADR 0027): admin content only."""
    _ = locale.gettext
    return Field(
        "label",
        _("admin.field.label"),
        "text",
        form.text("label"),
        hint=_("admin.field.label_hint"),
        error=form.error_for("label"),
        required=False,
        attributes={"maxlength": str(MAX_LABEL_LENGTH)},
    )


def capacity_field(
    form: FormData,
    locale: RequestLocale,
    default: str,
    required: bool = True,
    hints: Hints = SERVICE_HINTS,
) -> Field:
    """Return the agent-count input of a block, 0 to 1000 (0 for lunch, ADR 0027)."""
    _ = locale.gettext
    return Field(
        "capacity",
        _("admin.field.capacity"),
        "number",
        form.text("capacity") or default,
        hint=_(hints.capacity),
        error=form.error_for("capacity"),
        required=required,
        attributes={"min": "0", "max": "1000", "inputmode": "numeric"},
    )


def read_offer(form: FormData) -> str | None:
    """Return the submitted offer; a form without the field means the default (slots)."""
    if not form.text("offer"):
        return Offer.SCHEDULED.value
    return form.choice("offer", OFFER_VALUES)


def offer_field(form: FormData, locale: RequestLocale, hints: Hints = SERVICE_HINTS) -> Field:
    """Return the offer select of a block: slots, direct calls or both (ADR 0072)."""
    _ = locale.gettext
    return Field(
        "offer",
        _("admin.field.offer"),
        "select",
        form.text("offer") or Offer.SCHEDULED.value,
        hint=_(hints.offer),
        error=form.error_for("offer"),
        options=tuple(Option(offer.value, _(OFFER_LABELS[offer])) for offer in Offer),
    )


def hours_fields(
    form: FormData, locale: RequestLocale, hints: Hints = SERVICE_HINTS
) -> list[Field]:
    """Return the inputs of the add-block and change-block forms."""
    _ = locale.gettext
    days = tuple(Option(str(i), name) for i, name in enumerate(weekday_names(locale.code)))
    return [
        Field(
            "weekday",
            _("admin.field.weekday"),
            "select",
            form.text("weekday") or "0",
            error=form.error_for("weekday"),
            options=days,
        ),
        Field(
            "opens",
            _("admin.field.opens"),
            "time",
            form.text("opens") or "09:00",
            error=form.error_for("opens"),
        ),
        Field(
            "closes",
            _("admin.field.closes"),
            "time",
            form.text("closes") or "12:00",
            error=form.error_for("closes"),
        ),
        capacity_field(form, locale, "1", hints=hints),
        offer_field(form, locale, hints),
        label_field(form, locale),
        Field(
            "valid_from",
            _("admin.field.valid_from"),
            "date",
            form.text("valid_from"),
            error=form.error_for("valid_from"),
            required=False,
        ),
        Field(
            "valid_to",
            _("admin.field.valid_to"),
            "date",
            form.text("valid_to"),
            hint=_("admin.field.valid_hint"),
            error=form.error_for("valid_to"),
            required=False,
        ),
    ]


def split_fields(
    form: FormData, locale: RequestLocale, hints: Hints = SERVICE_HINTS
) -> list[Field]:
    """Return the inputs of the split form: the split time, agents and label of part two."""
    _ = locale.gettext
    return [
        Field(
            "split_at",
            _("admin.field.split_at"),
            "time",
            form.text("split_at"),
            hint=_("admin.field.split_at_hint"),
            error=form.error_for("split_at"),
        ),
        capacity_field(form, locale, "0", hints=hints),
        label_field(form, locale),
    ]


def exception_fields(
    form: FormData, locale: RequestLocale, hints: Hints = SERVICE_HINTS
) -> list[Field]:
    """Return the inputs of the add-exception form (the kind is a separate radio group)."""
    _ = locale.gettext
    return [
        Field("day", _("admin.field.day"), "date", form.text("day"), error=form.error_for("day")),
        Field(
            "opens",
            _("admin.field.opens"),
            "time",
            form.text("opens"),
            hint=_("admin.field.only_with_hours"),
            error=form.error_for("opens"),
            required=False,
        ),
        Field(
            "closes",
            _("admin.field.closes"),
            "time",
            form.text("closes"),
            error=form.error_for("closes"),
            required=False,
        ),
        capacity_field(form, locale, "", required=False, hints=hints),
        offer_field(form, locale, hints),
        label_field(form, locale),
    ]


def read_rule(form: FormData, subjects: SubjectReader | None = None) -> ScheduleRule | None:
    """Parse the window form into a rule; ``None`` (with form errors) when it is incomplete."""
    weekday = form.choice("weekday", WEEKDAYS)
    opens, closes = form.clock("opens"), form.clock("closes")
    capacity = form.integer("capacity")
    offer = read_offer(form)
    valid_from, valid_to = form.optional_day("valid_from"), form.optional_day("valid_to")
    if form.errors or not (weekday and opens and closes and offer and capacity is not None):
        return None
    try:
        window = TimeWindow(
            opens,
            closes,
            capacity,
            form.optional_text("label"),
            Offer(offer),
            subjects() if subjects else (),
        )
        return ScheduleRule(int(weekday), window, valid_from, valid_to)
    except DomainError as error:
        form.refusal(error)
        return None


def rule_values(rule: ScheduleRule) -> dict[str, str]:
    """Return a stored rule as form values, to pre-fill the change form."""
    return {
        "weekday": str(rule.weekday),
        "opens": rule.window.opens.strftime("%H:%M"),
        "closes": rule.window.closes.strftime("%H:%M"),
        "capacity": str(rule.window.capacity),
        "offer": rule.window.offer.value,
        "label": rule.window.label or "",
        "valid_from": rule.valid_from.isoformat() if rule.valid_from else "",
        "valid_to": rule.valid_to.isoformat() if rule.valid_to else "",
    }


def read_override(form: FormData, subjects: SubjectReader | None = None) -> ScheduleOverride | None:
    """Parse the exception form; ``None`` (with form errors) when it is incomplete."""
    day = form.day("day")
    kind = form.choice("kind", {"closed", "hours"})
    window = None
    if kind == "hours":
        opens, closes, capacity = (
            form.clock("opens"),
            form.clock("closes"),
            form.integer("capacity"),
        )
        offer = read_offer(form)
        if opens and closes and offer and capacity is not None:
            try:
                window = TimeWindow(
                    opens,
                    closes,
                    capacity,
                    form.optional_text("label"),
                    Offer(offer),
                    subjects() if subjects else (),
                )
            except DomainError as error:
                form.refusal(error)
    if form.errors or day is None:
        return None
    return ScheduleOverride(day, window)


def override_values(override: ScheduleOverride) -> dict[str, str]:
    """Return a stored exception as form values, to pre-fill the change form."""
    window = override.window
    values = {"day": override.day.isoformat(), "kind": "closed" if window is None else "hours"}
    if window is not None:
        values |= {
            "opens": window.opens.strftime("%H:%M"),
            "closes": window.closes.strftime("%H:%M"),
            "capacity": str(window.capacity),
            "offer": window.offer.value,
            "label": window.label or "",
        }
    return values
