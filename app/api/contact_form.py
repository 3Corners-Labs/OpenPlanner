"""The citizen's contact form, shared by booking and direct calls (HANDOVER B15).

Only name, email and (per group) phone; a hidden honeypot field catches simple bots. Values
are capped and re-filled into the citizen's own form on errors, never echoed elsewhere.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.scheduling import Group, PhoneMode
from app.web.errors import FormError
from app.web.locale import RequestLocale
from app.web.protection import RejectedRequest

HONEYPOT_FIELD = "website"
MAX_VALUE_LENGTH = 300


@dataclass(frozen=True)
class FieldView:
    """One input of the contact form, fully prepared for the template."""

    name: str
    type: str
    autocomplete: str
    label: str
    required: bool
    value: str
    hint: str | None
    error: str | None

    @property
    def described_by(self) -> str:
        """IDs of the hint and error message, for ``aria-describedby``."""
        ids = [f"{self.name}-hint" if self.hint else "", f"{self.name}-error" if self.error else ""]
        return " ".join(i for i in ids if i)


def contact_fields(
    group: Group, locale: RequestLocale, errors: list[FormError], values: dict[str, str]
) -> list[FieldView]:
    """Return the contact form inputs: name, email and (per group) phone (HANDOVER B15)."""
    _ = locale.gettext
    by_field = {error.field: error.message for error in errors if error.field}
    fields = [
        FieldView("name", "text", "name", _("form.name"), True, "", None, None),
        FieldView("email", "email", "email", _("form.email"), True, "", _("form.email_hint"), None),
    ]
    if group.phone_mode is not PhoneMode.HIDDEN:
        required = group.phone_mode is PhoneMode.REQUIRED
        fields.append(FieldView("phone", "tel", "tel", _("form.phone"), required, "", None, None))
    return [
        FieldView(
            f.name,
            f.type,
            f.autocomplete,
            f.label,
            f.required,
            values.get(f.name, ""),
            f.hint,
            by_field.get(f.name),
        )
        for f in fields
    ]


def read_contact(form: Mapping[str, object]) -> dict[str, str]:
    """Return the submitted contact values (capped).

    Raises:
        RejectedRequest: 400 if the honeypot field was filled in.
    """
    if form.get(HONEYPOT_FIELD):
        raise RejectedRequest(400)
    return {key: str(form.get(key, ""))[:MAX_VALUE_LENGTH] for key in ("name", "email", "phone")}
