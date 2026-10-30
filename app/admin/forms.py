"""Admin forms: reading submitted values and turning refusals into field messages.

Forms are plain HTML (no JavaScript, CSP without scripts). A ``FormData`` collects the submitted
strings and the parse errors; the domain validates the meaning. Every error is linked to its
input (``aria-describedby``, error summary with anchors) and never repeats the value.

    form = FormData(raw, _)
    minutes = form.integer("slot_minutes")      # None + error when not a whole number
    ... domain call ...  except DomainError as error: form.refusal(error)
"""

import re
from collections.abc import Iterable, Mapping, Sequence, Set
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, tzinfo
from urllib.parse import parse_qsl

from fastapi import Request
from starlette.responses import RedirectResponse

from app.admin.authz import AccessDenied
from app.domain.errors import DomainError, ErrorCode
from app.domain.theme import TOKENS, TokenKind
from app.i18n import BoundGettext
from app.i18n.dates import format_clock, format_day_compact, local_day
from app.web.errors import FormError
from app.web.locale import RequestLocale
from app.web.protection import RejectedRequest

MAX_FORM_BYTES = 16 * 1024
"""Admin forms are small; anything larger is refused before parsing."""

CLOCK_PATTERN = r"([01]?[0-9]|2[0-3])[:.][0-5][0-9]"
"""A 24-hour time such as ``9:05``, ``09:05`` or ``17.30``; also the inputs' ``pattern``."""

DAY_PATTERN = r"(0?[1-9]|[12][0-9]|3[01])[\-\/.](0?[1-9]|1[0-2])[\-\/.][0-9]{4}"
"""A day-month-year date such as ``03-04-2026``, ``3/4/2026`` or ``3.4.2026``.

Also the inputs' ``pattern``, so ``-`` and ``/`` are escaped for the browser's ``v`` flag.

Times and dates are plain text inputs: a native ``type="time"`` or ``type="date"`` follows the
browser locale (AM/PM, mm/dd/yyyy) whatever the page language, so the admin would see two
formats side by side. ISO ``YYYY-MM-DD`` is still read, for links and older bookmarks.
"""

_CLOCK = re.compile(CLOCK_PATTERN)
_DAY = re.compile(DAY_PATTERN)
_ISO_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

DONE_MESSAGES: dict[str, str] = {
    "created": "admin.done.created",
    "diagnostics_on": "admin.done.diagnostics_on",
    "diagnostics_off": "admin.done.diagnostics_off",
    "released": "admin.done.released",
    "saved": "admin.done.saved",
    "unchanged": "admin.done.unchanged",
    "added": "admin.done.added",
    "removed": "admin.done.removed",
    "logo_saved": "admin.done.logo_saved",
    "logo_removed": "admin.done.logo_removed",
    "font_saved": "admin.done.font_saved",
    "font_removed": "admin.done.font_removed",
    "theme_saved": "admin.done.theme_saved",
    "image_saved": "admin.done.image_saved",
    "image_removed": "admin.done.image_removed",
    "scenario_created": "admin.done.scenario_created",
    "scenario_deleted": "admin.done.scenario_deleted",
    "checked": "admin.done.checked",
    "maintenance_on": "admin.done.maintenance_on",
    "adopted": "admin.done.adopted",
    "login_mapping_saved": "admin.done.login_mapping_saved",
    "login_mapping_refused": "admin.done.login_mapping_refused",
    "maintenance_off": "admin.done.maintenance_off",
    "simulated": "admin.done.simulated",
    "simulated_prepared": "admin.done.simulated_prepared",
    "simulator_no_ids": "admin.done.simulator_no_ids",
    "simulator_no_secret": "admin.done.simulator_no_secret",  # nosec B105 - a catalogue key
    "simulator_refused": "admin.done.simulator_refused",
    "closure_started": "admin.done.closure_started",
    "closure_updated": "admin.done.closure_updated",
    "closure_lifted": "admin.done.closure_lifted",
    "reinstated": "admin.done.reinstated",
    "cancelled": "admin.done.cancelled",
    "moved": "admin.done.moved",
    "shortfall_accepted": "admin.done.shortfall_accepted",
    "rebook_requested": "admin.done.rebook_requested",
    "organisation_closed": "admin.done.organisation_closed",
    "organisation_lifted": "admin.done.organisation_lifted",
    "client_enabled": "admin.done.client_enabled",
    "client_disabled": "admin.done.client_disabled",
    "tokens_revoked": "admin.done.tokens_revoked",
    "update_requested": "admin.done.update_requested",
    "restore_requested": "admin.done.restore_requested",
    "request_cancelled": "admin.done.request_cancelled",
    "texts_saved": "admin.done.texts_saved",
    "test_sent": "admin.done.test_sent",
}
"""Confirmations after a change; only these keys are read from ``?done=``."""

FIELD_MESSAGES: dict[str, str] = {
    "name": "admin.form.error.name",
    "slug": "admin.form.error.slug",
    "organisation": "admin.form.error.organisation",
    "default_locale": "admin.form.error.default_locale",
    "timezone": "admin.form.error.timezone",
    "slot_minutes": "admin.form.error.slot_minutes",
    "min_notice_minutes": "admin.form.error.min_notice_minutes",
    "horizon_days": "admin.form.error.horizon_days",
    "dispatch_lead_minutes": "admin.form.error.dispatch_lead_minutes",
    "dispatch_kind": "admin.form.error.choice",
    "queue_change_working_days": "admin.form.error.queue_change_working_days",
    "planner_change_min_lead_minutes": "admin.form.error.planner_change_min_lead_minutes",
    "queue_visible_from_minutes": "admin.form.error.queue_visible_from_minutes",
    "queue_free_pick_allowed": "admin.form.error.choice",
    "max_bookings_per_email": "admin.form.error.max_bookings_per_email",
    "calendar_invite_address": "admin.form.error.calendar_invite_address",
    "phone_mode": "admin.form.error.phone_mode",
    "video_host_email": "admin.form.error.video_host_email",
    "wxcc_business_address": "admin.form.error.integration_text",
    "wxcc_channel": "admin.form.error.integration_text",
    "wxcc_routing_value": "admin.form.error.integration_text",
    "direct_call_enabled": "admin.form.error.choice",
    "embedding_enabled": "admin.form.error.choice",
    "not_picked_up_minutes": "admin.form.error.not_picked_up_minutes",
    "direct_call_max_concurrent": "admin.form.error.direct_call_max_concurrent",
    "display_name": "admin.form.error.display_name",
    "accent_color": "admin.form.error.accent_color",
    "logo": "admin.form.error.logo",
    "header_style": "admin.form.error.choice",
    **{
        spec.field: "admin.form.error.contrast"
        if spec.kind is TokenKind.COLOUR
        else "admin.form.error.token_range"
        for spec in TOKENS
    },
    "weekday": "admin.form.error.weekday",
    "opens": "admin.form.error.time",
    "closes": "admin.form.error.time",
    "window": "admin.form.error.window",
    "capacity": "admin.form.error.capacity",
    "label": "admin.form.error.label",
    "split_at": "admin.form.error.split_at",
    "valid_from": "admin.form.error.date",
    "valid_to": "admin.form.error.valid_to",
    "day": "admin.form.error.day",
    "schedule_rules": "admin.form.error.overlap",
    "schedule_overrides": "admin.form.error.override_conflict",
    "holiday_day": "admin.form.error.holiday_day",
    "year": "admin.form.error.year",
    "calendars": "admin.form.error.calendars",
    "source": "admin.form.error.choice",
    "starts_at": "admin.form.error.moment",
    "ends_at": "admin.form.error.ends_at",
    "expected_end": "admin.form.error.expected_end",
    "notice_nl": "admin.form.error.notice",
    "notice_en": "admin.form.error.notice",
    "heading_nl": "admin.form.error.closure_heading",
    "heading_en": "admin.form.error.closure_heading",
    "scenario_name": "admin.form.error.scenario_name",
    "alt_nl": "admin.form.error.image_alt",
    "alt_en": "admin.form.error.image_alt",
    "start": "admin.form.error.start",
    "scopes": "admin.form.error.scopes",
    "groups": "admin.form.error.groups",
    "networks": "admin.form.error.networks",
    "lifetime_days": "admin.form.error.lifetime_days",
    "smtp_host": "admin.form.error.smtp_host",
    "smtp_port": "admin.form.error.smtp_port",
    "smtp_security": "admin.form.error.smtp_security",
    "smtp_username": "admin.form.error.integration_text",
    "smtp_credential": "admin.form.error.smtp_credential",
    "sender_address": "admin.form.error.sender_address",
    "sender_name": "admin.form.error.sender_name",
    "sms_provider": "admin.form.error.sms_provider",
    "sms_api_base_url": "admin.form.error.sms_api_base_url",
    "sms_api_key": "admin.form.error.sms_api_key",
    "sms_sender_id": "admin.form.error.sms_sender_id",
    "sms_country_code": "admin.form.error.sms_country_code",
    "test_recipient": "admin.form.error.test_recipient",
    "channel": "admin.form.error.reminder_channel",
    "minutes_before": "admin.form.error.reminder_offset",
    "reminders": "admin.form.error.reminder_limit",
    "name_nl": "admin.form.error.role_name",
    "name_en": "admin.form.error.role_name",
    "claim_value": "admin.form.error.claim_value",
    "permissions": "admin.form.error.permissions",
    "template": "admin.form.error.choice",
}
"""Message per field for ``invalid_configuration``; the key is also the input's id."""

FIELD_TARGET: dict[str, str] = {
    "window": "closes",
    "schedule_rules": "opens",
    "schedule_overrides": "day",
}
"""Schedule-level refusals are linked to the input the admin most likely needs to change."""

CODE_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.SLUG_TAKEN: "admin.form.error.slug_taken",
    ErrorCode.ORGANISATION_NOT_FOUND: "admin.form.error.organisation",
    ErrorCode.GROUP_HAS_FUTURE_BOOKINGS: "admin.form.error.future_bookings",
    ErrorCode.DATABASE_BUSY: "error.database_busy",
    ErrorCode.HOLIDAY_CALENDAR_IN_USE: "admin.form.error.calendar_in_use",
    ErrorCode.CLOSURE_EXISTS: "admin.form.error.closure_exists",
    ErrorCode.CLOSURE_NOT_FOUND: "admin.form.error.closure_not_found",
    ErrorCode.SCENARIO_NOT_FOUND: "admin.form.error.scenario_not_found",
    ErrorCode.BOOKING_NOT_ACTIVE: "error.booking_not_active",
    ErrorCode.BOOKING_NOT_PENDING: "error.booking_not_pending",
    ErrorCode.BOOKING_LOCKED: "error.booking_locked",
    ErrorCode.GROUP_CLOSED: "error.group_closed",
    ErrorCode.SLOT_FULL: "error.slot_full",
    ErrorCode.BOOKING_LIMIT_REACHED: "admin.form.error.booking_limit_reached",
    ErrorCode.SLOT_UNAVAILABLE: "error.slot_unavailable",
    ErrorCode.OUTSIDE_BOOKING_WINDOW: "error.outside_booking_window",
    ErrorCode.SAME_SLOT: "error.same_slot",
    ErrorCode.PLANNER_TOO_LATE: "admin.form.error.planner_too_late",
    ErrorCode.MOVE_CAUSES_SHORTFALL: "admin.form.error.move_causes_shortfall",
    ErrorCode.SLOT_CHANGED: "admin.form.error.slot_changed",
    ErrorCode.REMINDER_EXISTS: "admin.form.error.reminder_exists",
    ErrorCode.INTEGRATION_NOT_READY: "admin.form.error.settings_key",
    ErrorCode.ROLE_CHANGED: "admin.form.error.role_changed",
    ErrorCode.MAINTENANCE_FROM_ENVIRONMENT: "admin.form.error.maintenance_from_environment",
    ErrorCode.SETTING_NOT_EDITABLE: "admin.form.error.setting_not_editable",
    ErrorCode.SETTING_LOCKED: "admin.form.error.setting_locked",
    ErrorCode.SETTING_INVALID: "admin.form.error.setting_invalid",
    ErrorCode.SETTING_COMBINATION: "admin.form.error.setting_combination",
    ErrorCode.SETTING_EXPIRY: "admin.form.error.setting_expiry",
    ErrorCode.SETTING_NO_KEY: "admin.form.error.setting_no_key",
    ErrorCode.SETTING_NOT_LOCKED: "admin.form.error.setting_not_locked",
    ErrorCode.LOGIN_LOCKOUT: "admin.form.error.login_lockout",
}


@dataclass(frozen=True)
class Option:
    """One choice of a select or radio group."""

    value: str
    label: str


def options(pairs: Iterable[tuple[str, str]]) -> tuple[Option, ...]:
    """Return select options from ``(value, label)`` pairs."""
    return tuple(Option(value, label) for value, label in pairs)


def moment_label(at: datetime, tz: tzinfo, locale: str) -> str:
    """Return a compact local date and time: ``5 jan 2026, 09:00``."""
    return f"{format_day_compact(local_day(at, tz), locale)}, {format_clock(at, tz, locale)}"


def slot_options(starts: Sequence[datetime], tz: tzinfo, locale: str) -> tuple[Option, ...]:
    """Return free start times as select options: the ISO value and ``5 jan 2026, 09:00``."""
    return tuple(Option(start.isoformat(), moment_label(start, tz, locale)) for start in starts)


@dataclass(frozen=True)
class Field:
    """Everything a template needs to render one labelled input accessibly.

    Attributes:
        name: Input name and id.
        label: Visible label.
        kind: ``text``, ``number``, ``time`` (24-hour text), ``date`` (dd-mm-yyyy text),
            ``datetime`` (a date input plus a 24-hour ``{name}_time`` input; value
            ``YYYY-MM-DDTHH:MM``), ``textarea`` or ``select``.
        value: Current or submitted value.
        hint: Help text, linked with ``aria-describedby``.
        error: Message for this field, linked the same way.
        options: Choices of a select.
        required: Whether the input is required.
        readonly: Shown but not editable (for example the slug).
        attributes: Extra input attributes such as ``min`` and ``step``.
        more: The input's section of the manual (rendered HTML), shown in an (i) disclosure
            (ADR 0033).
        more_link: The help page anchored at that section.
    """

    name: str
    label: str
    kind: str = "text"
    value: str = ""
    hint: str | None = None
    error: str | None = None
    options: tuple[Option, ...] = ()
    required: bool = True
    readonly: bool = False
    attributes: Mapping[str, str] = field(default_factory=dict)
    more: str | None = None
    more_link: str | None = None

    @property
    def described_by(self) -> str:
        """Return the ids of hint and error, for ``aria-describedby``."""
        ids = [f"{self.name}-hint" if self.hint else "", f"{self.name}-error" if self.error else ""]
        return " ".join(i for i in ids if i)

    @property
    def clock_pattern(self) -> str:
        """Return the ``pattern`` of a 24-hour time input (``CLOCK_PATTERN``)."""
        return CLOCK_PATTERN

    @property
    def day_pattern(self) -> str:
        """Return the ``pattern`` of a date input (``DAY_PATTERN``)."""
        return DAY_PATTERN

    @property
    def date_text(self) -> str:
        """Return the value of a ``date`` field as dd-mm-yyyy; typed text is kept as is."""
        return _day_text(self.value)

    @property
    def moment_parts(self) -> tuple[str, str]:
        """Return the date (dd-mm-yyyy) and time of a ``datetime`` field."""
        day, _, clock = self.value.partition("T")
        return _day_text(day), clock


class FormData:
    """Submitted form values with parse errors collected per field.

    Args:
        values: The submitted strings (already stripped of surrounding white space here).
        gettext: Translates error messages.
    """

    def __init__(self, values: Mapping[str, str], gettext: BoundGettext) -> None:
        """Keep the trimmed values; no error yet."""
        self.values = {key: value.strip() for key, value in values.items()}
        self.errors: list[FormError] = []
        self._ = gettext

    def text(self, name: str) -> str:
        """Return the submitted text of ``name`` ('' when absent)."""
        return self.values.get(name, "")

    def optional_text(self, name: str) -> str | None:
        """Return the submitted text, or ``None`` when empty."""
        return self.text(name) or None

    def integer(self, name: str) -> int | None:
        """Return a whole number, or ``None`` with an error for ``name``."""
        raw = self.text(name)
        if raw.isdigit() and len(raw) <= 6:
            return int(raw)
        self._fail(name)
        return None

    def optional_integer(self, name: str) -> int | None:
        """Return a whole number, or ``None`` when empty (an error when not a number)."""
        return self.integer(name) if self.text(name) else None

    def clock(self, name: str) -> time | None:
        """Return a 24-hour time of day (see ``CLOCK_PATTERN``), or ``None`` with an error."""
        parsed = _parse_clock(self.text(name))
        if parsed is None:
            self._fail(name, "admin.form.error.time")
        return parsed

    def day(self, name: str) -> date | None:
        """Return a date (see ``DAY_PATTERN``), or ``None`` with an error."""
        parsed = _parse_day(self.text(name))
        if parsed is None:
            self._fail(name, "admin.form.error.date")
        return parsed

    def optional_day(self, name: str) -> date | None:
        """Return a date, ``None`` when empty, or ``None`` with an error when malformed."""
        return self.day(name) if self.text(name) else None

    def optional_moment(self, name: str, tz: tzinfo) -> datetime | None:
        """Return the ``datetime`` field ``name`` in ``tz`` as UTC; ``None`` if empty or malformed.

        The day comes from ``name`` and the time from ``{name}_time``; both or neither. Parsing
        happens here, at the edge, so the domain only sees UTC (CLAUDE.md).
        """
        raw_day, raw_clock = self.text(name), self.text(f"{name}_time")
        if not raw_day and not raw_clock:
            return None
        day, clock = _parse_day(raw_day), _parse_clock(raw_clock)
        if day is None or clock is None:
            self._fail(name, "admin.form.error.moment")
            return None
        return datetime.combine(day, clock, tz).astimezone(UTC)

    def moment_value(self, name: str, default: str = "") -> str:
        """Return a submitted ``datetime`` field as ``dayTtime`` to re-render, or ``default``."""
        if name not in self.values and f"{name}_time" not in self.values:
            return default
        return f"{self.text(name)}T{self.text(f'{name}_time')}"

    def checked(self, name: str) -> bool:
        """Return whether the checkbox ``name`` was ticked."""
        return self.text(name) == "yes"

    def choice(self, name: str, allowed: Set[str]) -> str | None:
        """Return the submitted value if it is one of ``allowed``; else ``None`` with an error."""
        value = self.text(name)
        if value in allowed:
            return value
        self._fail(name)
        return None

    def refusal(self, error: DomainError) -> None:
        """Add the message for a domain refusal, linked to its field where it has one."""
        if error.code is ErrorCode.INVALID_CONFIGURATION and error.field in FIELD_MESSAGES:
            linked = FIELD_TARGET.get(error.field, error.field)
            self.errors.append(FormError(linked, self._(FIELD_MESSAGES[error.field])))
            return
        target = error.field if error.field in FIELD_MESSAGES else None
        if error.code is ErrorCode.ORGANISATION_NOT_FOUND:
            target = "organisation"
        message = CODE_MESSAGES.get(error.code, "error.invalid_request")
        self.errors.append(FormError(target, self._(message)))

    def error_for(self, name: str) -> str | None:
        """Return the first message linked to ``name``."""
        return next((e.message for e in self.errors if e.field == name), None)

    def _fail(self, name: str, key: str | None = None) -> None:
        """Record that ``name`` could not be read, with ``key`` or the field's own message."""
        message = key or FIELD_MESSAGES.get(name, "error.invalid_request")
        self.errors.append(FormError(name, self._(message)))


def _parse_clock(raw: str) -> time | None:
    """Return the 24-hour time in ``raw``, or ``None`` when it is not one."""
    if not _CLOCK.fullmatch(raw):
        return None
    hours, minutes = re.split(r"[:.]", raw)
    return time(int(hours), int(minutes))


def _parse_day(raw: str) -> date | None:
    """Return the date in ``raw`` (day-month-year or ISO), or ``None`` when it is not one."""
    if _ISO_DAY.fullmatch(raw):
        parts = raw.split("-")
    elif _DAY.fullmatch(raw):
        parts = re.split(r"[-/.]", raw)[::-1]
    else:
        return None
    year, month, day = (int(part) for part in parts)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _day_text(value: str) -> str:
    """Return an ISO date as dd-mm-yyyy for an input; anything else unchanged."""
    if not _ISO_DAY.fullmatch(value):
        return value
    year, month, day = value.split("-")
    return f"{day}-{month}-{year}"


def done_message(request: Request, _: BoundGettext) -> str | None:
    """Return the confirmation named by ``?done=``, if it is a known one.

    ``?count=`` (digits only) fills a ``{count}`` in the confirmation, e.g. how many
    bookings an emergency closure suspended.
    """
    key = request.query_params.get("done", "")
    raw = request.query_params.get("count", "")
    count = int(raw) if raw.isdigit() and len(raw) <= 6 else 0
    return _(DONE_MESSAGES[key], count=count) if key in DONE_MESSAGES else None


def safe_next(value: str | None) -> str:
    """Return ``value`` if it is an admin path on this site; otherwise the dashboard.

    For every "return to" target that arrives with a request (login, re-login, the update
    banner's snooze), so none can send the admin off the portal.
    """
    if (
        value
        and value.startswith("/admin")
        and not value.startswith("//")
        and "\\" not in value
        and "://" not in value
    ):
        return value
    return "/admin/"


def redirect_done(
    url: str, done: str, count: int | None = None, *, anchor: str = ""
) -> RedirectResponse:
    """Answer a successful change with a redirect to ``url`` and its confirmation.

    ``url`` may carry a query of its own (``?year=2027``); ``done`` (and ``count``, for
    confirmations that state a number) are appended to it. ``anchor`` scrolls the page to
    the part the confirmation is about.
    """
    separator = "&" if "?" in url else "?"
    suffix = f"&count={count}" if count is not None else ""
    fragment = f"#{anchor}" if anchor else ""
    return RedirectResponse(f"{url}{separator}done={done}{suffix}{fragment}", status_code=303)


async def read_form(request: Request, locale: RequestLocale) -> FormData:
    """Read a urlencoded form body (only the first value of every name).

    Raises:
        RejectedRequest: 413 for bodies above ``MAX_FORM_BYTES``.
    """
    raw = await request.body()
    if len(raw) > MAX_FORM_BYTES:
        raise RejectedRequest(413)
    body = raw.decode("utf-8", errors="replace")
    values: dict[str, str] = {}
    for key, value in parse_qsl(body, keep_blank_values=True):
        values.setdefault(key, value)
    return FormData(values, locale.gettext)


def raise_refusal(error: DomainError) -> None:
    """Turn refusals that are not form errors into the portal's responses.

    Raises:
        AccessDenied: For ``not_permitted`` (the domain re-checked the matrix).
    """
    if error.code is ErrorCode.NOT_PERMITTED:
        raise AccessDenied from error
