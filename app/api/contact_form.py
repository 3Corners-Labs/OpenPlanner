"""The participant's contact form, shared by booking, direct calls and the widget (HANDOVER B15).

First and last name, email, (per group) phone, and the group's custom fields (ADR 0055).
Custom inputs are named ``q_<key>`` so they never collide with the system fields or the
honeypot. A hidden honeypot field catches simple bots, which becomes a security event
(ADR 0053). Values are capped and re-filled into the participant's own form on errors, never
echoed elsewhere.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.domain.booking import Contact
from app.domain.errors import DomainError, ErrorCode
from app.domain.form_fields import (
    LONG_TEXT_MAX_LENGTH,
    AnswerValue,
    FieldDefinition,
    FieldType,
    FormDefinition,
    Purpose,
)
from app.domain.routing import ANSWER_PREFIX as ROUTING_PREFIX
from app.domain.routing import GroupRouting
from app.domain.scheduling import Group, PhoneMode
from app.domain.security_log import HONEYPOT
from app.domain.services import BookingService, ContactInput
from app.web.errors import FormError, form_errors
from app.web.locale import RequestLocale
from app.web.protection import RejectedRequest
from app.web.routing_labels import option_label, source_label

HONEYPOT_FIELD = "website"
MAX_VALUE_LENGTH = 300
ANSWER_PREFIX = "q_"
SYSTEM_FIELDS = ("first_name", "last_name", "email", "phone")
SUBJECT_INPUT = f"{ANSWER_PREFIX}_subject"
"""The input of the subject question (ADR 0081): ``q_`` plus the routing answer key."""

# The HTML input of each type: (kind, type, autocomplete, inputmode).
_INPUTS: dict[FieldType, tuple[str, str, str, str | None]] = {
    FieldType.SHORT_TEXT: ("input", "text", "off", None),
    FieldType.LONG_TEXT: ("textarea", "text", "off", None),
    FieldType.EMAIL: ("input", "email", "email", None),
    FieldType.PHONE: ("input", "tel", "tel", None),
    FieldType.NUMBER: ("input", "text", "off", "numeric"),
    FieldType.DATE: ("input", "date", "off", None),
    FieldType.POSTCODE_NL: ("input", "text", "postal-code", None),
    # Radio buttons, not a select: easier to use (GOV.UK, NL Design System), and WebKit's
    # select inside the widget's shadow root breaks a strict host CSP (ADR 0055).
    FieldType.CHOICE: ("radio", "radio", "off", None),
    FieldType.CHECKBOX: ("checkbox", "checkbox", "off", None),
}


@dataclass(frozen=True)
class FieldView:
    """One input of the contact form, fully prepared for the template.

    ``kind`` is ``input``, ``textarea``, ``radio`` or ``checkbox``; ``options`` are the
    ``(value, label)`` pairs of a radio group; ``minimum``/``maximum`` its ``min``/``max``.
    """

    name: str
    type: str
    autocomplete: str
    label: str
    required: bool
    value: str
    hint: str | None
    error: str | None
    kind: str = "input"
    options: tuple[tuple[str, str], ...] = ()
    minimum: str | None = None
    maximum: str | None = None
    inputmode: str | None = None

    @property
    def described_by(self) -> str:
        """IDs of the hint and error message, for ``aria-describedby``."""
        ids = [f"{self.name}-hint" if self.hint else "", f"{self.name}-error" if self.error else ""]
        return " ".join(i for i in ids if i)


@dataclass(frozen=True)
class ContactForm:
    """A group's contact form as it stands now: its custom fields and the group's day.

    Build it with ``load_contact_form``; the day bounds the date fields (ADR 0055).
    """

    group: Group
    form: FormDefinition
    today: date

    def fields(
        self, locale: RequestLocale, errors: list[FormError], values: Mapping[str, str]
    ) -> list[FieldView]:
        """Return the inputs: the routing questions, the system fields, the custom fields.

        The subject is the first question (ADR 0081 point 2), then the switched-on routing
        attributes; each is a radio group of codes, so nothing typed can reach the routing.
        """
        _ = locale.gettext
        by_field = {error.field: error.message for error in errors if error.field}
        email_hint = _("form.email_hint")
        fields = routing_fields(self.group.routing, locale)
        fields += [
            FieldView(
                "first_name", "text", "given-name", _("form.first_name"), True, "", None, None
            ),
            FieldView(
                "last_name", "text", "family-name", _("form.last_name"), True, "", None, None
            ),
            FieldView("email", "email", "email", _("form.email"), True, "", email_hint, None),
        ]
        if self.group.phone_mode is not PhoneMode.HIDDEN:
            required = self.group.phone_mode is PhoneMode.REQUIRED
            fields.append(
                FieldView("phone", "tel", "tel", _("form.phone"), required, "", None, None)
            )
        fields += [_custom(field, locale.code, self.today) for field in self.form.fields]
        return [_with(f, values.get(f.name, ""), by_field.get(f.name)) for f in fields]

    def correction_fields(
        self, locale: RequestLocale, errors: list[FormError], values: Mapping[str, str]
    ) -> list[FieldView]:
        """Return the inputs a participant may correct (ADR 0056 point 10).

        The booking form's fields without the routing questions (the subject stays) and without
        the email address, which cannot change: another address is another booking.
        """
        skip = {f.name for f in routing_fields(self.group.routing, locale)} | {"email"}
        return [f for f in self.fields(locale, errors, values) if f.name not in skip]

    def errors(self, error: DomainError, locale: RequestLocale) -> list[FormError]:
        """Return the messages for ``error``; a refused answer names its field's label."""
        if error.code is ErrorCode.INVALID_ANSWER and error.field in routing_keys(self.group):
            source = str(error.field).removeprefix(ROUTING_PREFIX)
            label = source_label(source, locale.gettext)
            message = locale.gettext("error.invalid_answer_field", label=label)
            return [FormError(f"{ANSWER_PREFIX}{error.field}", message)]
        if error.code is ErrorCode.INVALID_ANSWER and error.field:
            field = self.form.get(error.field)
            if field is not None:
                label = field.label(locale.code)
                message = locale.gettext("error.invalid_answer_field", label=label)
                return [FormError(f"{ANSWER_PREFIX}{field.key}", message)]
        return form_errors(error, locale.gettext)


async def load_contact_form(service: BookingService, group: Group, now: datetime) -> ContactForm:
    """Return ``group``'s contact form, with the day in the group's time zone."""
    return ContactForm(group, await service.get_form(group), now.astimezone(group.tz).date())


def _custom(field: FieldDefinition, locale: str, today: date) -> FieldView:
    """Return the input of one custom field."""
    kind, kind_type, autocomplete, inputmode = _INPUTS[field.type]
    minimum = maximum = None
    if field.type is FieldType.DATE:
        minimum = _day(today, field.minimum)
        maximum = _day(today, field.maximum)
    options = tuple((o, field.option_label(o, locale)) for o in field.options)
    return FieldView(
        f"{ANSWER_PREFIX}{field.key}",
        kind_type,
        autocomplete,
        field.label(locale),
        field.required,
        "",
        field.hint(locale) or None,
        None,
        kind,
        options,
        minimum,
        maximum,
        inputmode,
    )


def routing_keys(group: Group) -> frozenset[str]:
    """Return the answer keys of the group's routing questions (``_subject``, ``_<attr>``)."""
    return frozenset(f"{ROUTING_PREFIX}{source}" for source in group.routing.sources)


def routing_fields(routing: GroupRouting, locale: RequestLocale) -> list[FieldView]:
    """Return the radio groups of the subject and the switched-on attributes, in that order."""
    _ = locale.gettext
    views: list[FieldView] = []
    for source in routing.sources:
        options = tuple(
            (code, option_label(routing, source, code, _, locale.code))
            for code in routing.codes(source)
        )
        views.append(
            FieldView(
                f"{ANSWER_PREFIX}{ROUTING_PREFIX}{source}",
                "radio",
                "off",
                source_label(source, _),
                True,
                "",
                None,
                None,
                "radio",
                options,
            )
        )
    return views


def _day(today: date, days: int | None) -> str | None:
    """Return ``today`` plus ``days`` as an ISO date, or ``None``."""
    return None if days is None else (today + timedelta(days=days)).isoformat()


def _with(view: FieldView, value: str, error: str | None) -> FieldView:
    """Return ``view`` with the participant's value and the error of its field."""
    return FieldView(
        view.name,
        view.type,
        view.autocomplete,
        view.label,
        view.required,
        value,
        view.hint,
        error,
        view.kind,
        view.options,
        view.minimum,
        view.maximum,
        view.inputmode,
    )


def read_contact(form: Mapping[str, object]) -> dict[str, str]:
    """Return the submitted contact values and answers (capped), keyed by input name.

    Raises:
        RejectedRequest: 400 if the honeypot field was filled in.
    """
    if form.get(HONEYPOT_FIELD):
        raise RejectedRequest(400, HONEYPOT)
    values = {key: str(form.get(key, ""))[:MAX_VALUE_LENGTH] for key in SYSTEM_FIELDS}
    for key, value in form.items():
        if isinstance(key, str) and key.startswith(ANSWER_PREFIX):
            values[key] = str(value)[: LONG_TEXT_MAX_LENGTH + 1]
    return values


def contact_input(values: Mapping[str, str]) -> ContactInput:
    """Return the domain input for the values ``read_contact`` returned."""
    answers = {
        key.removeprefix(ANSWER_PREFIX): value
        for key, value in values.items()
        if key.startswith(ANSWER_PREFIX)
    }
    return ContactInput(
        values.get("first_name", ""),
        values.get("last_name", ""),
        values.get("email", ""),
        values.get("phone") or None,
        answers,
    )


def contact_values(contact: Contact) -> dict[str, str]:
    """Return a booking's contact as the form's input values, to prefill a correction.

    The answers are read for the participant's own view (``Purpose.PARTICIPANT_SELF``).
    """
    values = {
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "phone": contact.phone or "",
    }
    answers = contact.answers.visible(Purpose.PARTICIPANT_SELF) if contact.answers else {}
    for key, value in answers.items():
        values[f"{ANSWER_PREFIX}{key}"] = _input_value(value)
    return values


def _input_value(value: AnswerValue) -> str:
    """Return a stored answer as the form input's value (a ticked checkbox is ``on``)."""
    if isinstance(value, bool):
        return "on" if value else ""
    return str(value)
