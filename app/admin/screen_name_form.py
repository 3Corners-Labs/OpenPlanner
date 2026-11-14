"""The screen name's form field and its refusal texts, shared by both places that set it (ADR 0123).

A staff member's screen name is set in two places: by a manager on the person's page
(``app.admin.staff_routes``, step 5c) and by the person on *My profile*
(``app.admin.profile_routes``, step 5d). Both show the same input with the same label, hint and
length limit, and answer a refused text with the same sentence tied to the input, so those live
here once. The rule itself is ``app.domain.screen_name``; this module only words it.
"""

from fastapi import Request

from app.admin.forms import Field, FormData
from app.admin.pages import with_manual
from app.domain.screen_name import (
    MAX_LENGTH,
    MAX_MARKS,
    MIN_LENGTH,
    ScreenNameError,
    ScreenNameRefusal,
)
from app.domain.staff_screen_names import ScreenNameResult
from app.web.errors import FormError
from app.web.locale import RequestLocale

FIELD = "screen_name"
"""The name of the input and of the form key."""

DONE = ("staff_screen_name_saved", "staff_screen_name_cleared", "staff_screen_name_unchanged")
"""The confirmations of a save of the screen name; each page shows them in its own section."""

REFUSAL_TEXTS: dict[ScreenNameRefusal, str] = {
    ScreenNameRefusal.TOO_SHORT: "admin.staff.screen_name.error.too_short",
    ScreenNameRefusal.TOO_LONG: "admin.staff.screen_name.error.too_long",
    ScreenNameRefusal.BAD_CHARACTER: "admin.staff.screen_name.error.bad_character",
    ScreenNameRefusal.NO_LETTER: "admin.staff.screen_name.error.no_letter",
    ScreenNameRefusal.BAD_MARKS: "admin.staff.screen_name.error.bad_marks",
}
"""The catalogue key of each refusal of the rule for a screen name."""


def done_key(result: ScreenNameResult) -> str:
    """Return the confirmation key of a successful write: set, cleared or unchanged."""
    if result is ScreenNameResult.DONE:
        return "staff_screen_name_saved"
    if result is ScreenNameResult.CLEARED:
        return "staff_screen_name_cleared"
    return "staff_screen_name_unchanged"


def screen_name_field(
    request: Request, locale: RequestLocale, current: str | None, form: FormData | None
) -> Field:
    """Return the input of the screen name, with its hint and the manual's help.

    Args:
        request: The request (the manual topic follows from its route).
        locale: The language of the page.
        current: The stored name, shown when no refused text is kept.
        form: The refused form, whose typed text and error are shown again; ``None`` for a GET.
    """
    field = Field(
        FIELD,
        locale.gettext("admin.staff.screen_name.label"),
        # A refused text is shown again, cut to what any answer to it needs (the body may be 16 KB).
        value=form.text(FIELD)[: 2 * MAX_LENGTH] if form is not None else current or "",
        hint=locale.gettext("admin.staff.screen_name.hint"),
        error=form.error_for(FIELD) if form is not None else None,
        required=False,
        attributes={"maxlength": str(MAX_LENGTH), "autocomplete": "off"},
    )
    return with_manual(request, locale, [field])[0]


def refusal_error(locale: RequestLocale, error: ScreenNameError) -> FormError:
    """Return the error of the input for a text the rule refused (never the text itself)."""
    return FormError(
        FIELD,
        locale.gettext(
            REFUSAL_TEXTS[error.reason], min=MIN_LENGTH, max=MAX_LENGTH, marks=MAX_MARKS
        ),
    )
