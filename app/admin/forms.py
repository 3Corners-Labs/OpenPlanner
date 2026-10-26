"""Admin forms: reading submitted values and turning refusals into field messages.

Forms are plain HTML (no JavaScript, CSP without scripts). A ``FormData`` collects the submitted
strings and the parse errors; the domain validates the meaning. Every error is linked to its
input (``aria-describedby``, error summary with anchors) and never repeats the value.

    form = FormData(raw, _)
    minutes = form.integer("slot_minutes")      # None + error when not a whole number
    ... domain call ...  except DomainError as error: form.refusal(error)
"""

from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, tzinfo
from urllib.parse import parse_qsl

from fastapi import Request
from starlette.responses import RedirectResponse

from app.admin.authz import AccessDenied
from app.domain.errors import DomainError, ErrorCode
from app.i18n import BoundGettext
from app.i18n.dates import format_clock, format_day_compact, local_day
from app.web.errors import FormError
from app.web.locale import RequestLocale
from app.web.protection import RejectedRequest

MAX_FORM_BYTES = 16 * 1024
"""Admin forms are small; anything larger is refused before parsing."""

DONE_MESSAGES: dict[str, str] = {
    "created": "admin.done.created",
    "saved": "admin.done.saved",
    "unchanged": "admin.done.unchanged",
    "added": "admin.done.added",
    "removed": "admin.done.removed",
    "logo_saved": "admin.done.logo_saved",
    "logo_removed": "admin.done.logo_removed",
    "closure_started": "admin.done.closure_started",
    "closure_updated": "admin.done.closure_updated",
    "closure_lifted": "admin.done.closure_lifted",
    "reinstated": "admin.done.reinstated",
    "cancelled": "admin.done.cancelled",
    "moved": "admin.done.moved",
    "organisation_closed": "admin.done.organisation_closed",
    "organisation_lifted": "admin.done.organisation_lifted",
    "client_enabled": "admin.done.client_enabled",
    "client_disabled": "admin.done.client_disabled",
    "tokens_revoked": "admin.done.tokens_revoked",
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
    "phone_mode": "admin.form.error.phone_mode",
    "video_host_email": "admin.form.error.video_host_email",
    "wxcc_business_address": "admin.form.error.integration_text",
    "wxcc_channel": "admin.form.error.integration_text",
    "wxcc_routing_value": "admin.form.error.integration_text",
    "direct_call_enabled": "admin.form.error.choice",
    "not_picked_up_minutes": "admin.form.error.not_picked_up_minutes",
    "direct_call_max_concurrent": "admin.form.error.direct_call_max_concurrent",
    "display_name": "admin.form.error.display_name",
    "accent_color": "admin.form.error.accent_color",
    "logo": "admin.form.error.logo",
    "weekday": "admin.form.error.weekday",
    "opens": "admin.form.error.time",
    "closes": "admin.form.error.time",
    "window": "admin.form.error.window",
    "capacity": "admin.form.error.capacity",
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
    "notice_nl": "admin.form.error.notice",
    "notice_en": "admin.form.error.notice",
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
    "sms_api_base_url": "admin.form.error.sms_api_base_url",
    "sms_service_key": "admin.form.error.sms_service_key",
    "sms_sender_id": "admin.form.error.sms_sender_id",
    "sms_country_code": "admin.form.error.sms_country_code",
    "test_recipient": "admin.form.error.test_recipient",
    "channel": "admin.form.error.reminder_channel",
    "minutes_before": "admin.form.error.reminder_offset",
    "reminders": "admin.form.error.reminder_limit",
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
    ErrorCode.BOOKING_NOT_ACTIVE: "error.booking_not_active",
    ErrorCode.BOOKING_LOCKED: "error.booking_locked",
    ErrorCode.GROUP_CLOSED: "error.group_closed",
    ErrorCode.SLOT_FULL: "error.slot_full",
    ErrorCode.SLOT_UNAVAILABLE: "error.slot_unavailable",
    ErrorCode.OUTSIDE_BOOKING_WINDOW: "error.outside_booking_window",
    ErrorCode.SAME_SLOT: "error.same_slot",
    ErrorCode.REMINDER_EXISTS: "admin.form.error.reminder_exists",
    ErrorCode.INTEGRATION_NOT_READY: "admin.form.error.settings_key",
}


@dataclass(frozen=True)
class Option:
    """One choice of a select or radio group."""

    value: str
    label: str


def slot_options(starts: Sequence[datetime], tz: tzinfo, locale: str) -> tuple[Option, ...]:
    """Return free start times as select options: the ISO value and ``5 jan 2026, 09:00``."""
    return tuple(
        Option(
            start.isoformat(),
            f"{format_day_compact(local_day(start, tz), locale)}, "
            f"{format_clock(start, tz, locale)}",
        )
        for start in starts
    )


@dataclass(frozen=True)
class Field:
    """Everything a template needs to render one labelled input accessibly.

    Attributes:
        name: Input name and id.
        label: Visible label.
        kind: ``text``, ``number``, ``time``, ``date``, ``datetime-local``, ``textarea`` or
            ``select``.
        value: Current or submitted value.
        hint: Help text, linked with ``aria-describedby``.
        error: Message for this field, linked the same way.
        options: Choices of a select.
        required: Whether the input is required.
        readonly: Shown but not editable (for example the slug).
        attributes: Extra input attributes such as ``min`` and ``step``.
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

    @property
    def described_by(self) -> str:
        """Return the ids of hint and error, for ``aria-describedby``."""
        ids = [f"{self.name}-hint" if self.hint else "", f"{self.name}-error" if self.error else ""]
        return " ".join(i for i in ids if i)


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
        """Return a time of day (``HH:MM``), or ``None`` with an error."""
        try:
            return time.fromisoformat(self.text(name))
        except ValueError:
            self._fail(name)
            return None

    def day(self, name: str) -> date | None:
        """Return a date (``YYYY-MM-DD``), or ``None`` with an error."""
        try:
            return date.fromisoformat(self.text(name))
        except ValueError:
            self._fail(name)
            return None

    def optional_day(self, name: str) -> date | None:
        """Return a date, ``None`` when empty, or ``None`` with an error when malformed."""
        return self.day(name) if self.text(name) else None

    def optional_moment(self, name: str, tz: tzinfo) -> datetime | None:
        """Return a local ``YYYY-MM-DDTHH:MM`` in ``tz`` as UTC; ``None`` when empty or malformed.

        Parsing happens here, at the edge, so the domain only sees UTC (CLAUDE.md).
        """
        raw = self.text(name)
        if not raw:
            return None
        try:
            local = datetime.fromisoformat(raw)
        except ValueError:
            self._fail(name)
            return None
        if local.tzinfo is not None:
            self._fail(name)
            return None
        return local.replace(tzinfo=tz).astimezone(UTC)

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

    def _fail(self, name: str) -> None:
        """Record that ``name`` could not be read."""
        key = FIELD_MESSAGES.get(name, "error.invalid_request")
        self.errors.append(FormError(name, self._(key)))


def done_message(request: Request, _: BoundGettext) -> str | None:
    """Return the confirmation named by ``?done=``, if it is a known one.

    ``?count=`` (digits only) fills a ``{count}`` in the confirmation, e.g. how many
    bookings an emergency closure suspended.
    """
    key = request.query_params.get("done", "")
    raw = request.query_params.get("count", "")
    count = int(raw) if raw.isdigit() and len(raw) <= 6 else 0
    return _(DONE_MESSAGES[key], count=count) if key in DONE_MESSAGES else None


def redirect_done(url: str, done: str, count: int | None = None) -> RedirectResponse:
    """Answer a successful change with a redirect to ``url`` and its confirmation.

    ``url`` may carry a query of its own (``?year=2027``); ``done`` (and ``count``, for
    confirmations that state a number) are appended to it.
    """
    separator = "&" if "?" in url else "?"
    suffix = f"&count={count}" if count is not None else ""
    return RedirectResponse(f"{url}{separator}done={done}{suffix}", status_code=303)


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
