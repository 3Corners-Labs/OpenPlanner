"""Admin forms: reading submitted values and turning refusals into field messages.

Forms are plain HTML (no JavaScript, CSP without scripts). A ``FormData`` collects the submitted
strings and the parse errors; the domain validates the meaning. Every error is linked to its
input (``aria-describedby``, error summary with anchors) and never repeats the value.

    form = FormData(raw, _)
    minutes = form.integer("slot_minutes")      # None + error when not a whole number
    ... domain call ...  except DomainError as error: form.refusal(error)
"""

from collections.abc import Mapping, Set
from dataclasses import dataclass, field
from datetime import date, time
from urllib.parse import parse_qsl

from fastapi import Request
from starlette.responses import RedirectResponse

from app.admin.authz import AccessDenied
from app.domain.errors import DomainError, ErrorCode
from app.i18n import BoundGettext
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
}


@dataclass(frozen=True)
class Option:
    """One choice of a select or radio group."""

    value: str
    label: str


@dataclass(frozen=True)
class Field:
    """Everything a template needs to render one labelled input accessibly.

    Attributes:
        name: Input name and id.
        label: Visible label.
        kind: ``text``, ``number``, ``time``, ``date`` or ``select``.
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
    """Return the confirmation named by ``?done=``, if it is a known one."""
    key = request.query_params.get("done", "")
    return _(DONE_MESSAGES[key]) if key in DONE_MESSAGES else None


def redirect_done(url: str, done: str) -> RedirectResponse:
    """Answer a successful change with a redirect to ``url`` and its confirmation."""
    return RedirectResponse(f"{url}?done={done}", status_code=303)


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
