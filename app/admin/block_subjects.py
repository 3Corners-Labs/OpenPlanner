"""The subjects a staffing block offers, on the block and exception forms (ADR 0081 point 4).

A group with subjects shows one checkbox per subject on the forms of a weekly block and an
exception; none ticked offers every subject, as a block did before. The checkboxes are named
``subject_<code>``, so a form posts each as its own field.
"""

from collections.abc import Mapping

from app.admin.forms import FormData
from app.domain.scheduling import Group, TimeWindow
from app.web.locale import RequestLocale

PREFIX = "subject_"


def read_block_subjects(form: FormData, group: Group) -> tuple[str, ...]:
    """Return the ticked subject codes of ``group``, sorted; unknown codes are not read."""
    return tuple(sorted(s.code for s in group.routing.subjects if form.checked(PREFIX + s.code)))


def block_subject_values(window: TimeWindow | None) -> Mapping[str, str]:
    """Return a stored block's subjects as form values, to pre-fill a change form."""
    return {PREFIX + code: "yes" for code in window.subjects} if window else {}


def subject_choices(form: FormData, group: Group, locale: RequestLocale) -> list[dict[str, object]]:
    """Return the checkboxes of the group's subjects, ticked as the form says."""
    return [
        {
            "name": PREFIX + s.code,
            "label": s.label(locale.code),
            "checked": form.checked(PREFIX + s.code),
        }
        for s in group.routing.subjects
    ]
