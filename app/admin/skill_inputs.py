"""Inputs shared by the screens of skills, capacity pools and staff skill grants (ADR 0121).

A skill choice is a checkbox named ``skill-<id>`` (one value per name, like the holiday
calendars). The same helpers show the label of a skill in the admin's language, turn
the ticked boxes into ids and map the domain's refusals of those screens to form errors.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import NoReturn

from app.admin.forms import FormData, raise_refusal
from app.domain.errors import DomainError, ErrorCode
from app.domain.skills import Skill
from app.web.errors import FormError
from app.web.locale import RequestLocale
from app.web.protection import RejectedRequest

CHOICE_PREFIX = "skill-"
"""Input name of a skill checkbox is this prefix and the skill's id."""

_MAX_ID_DIGITS = 9

_UNKNOWN = frozenset(
    {
        ErrorCode.CONFIGURATION_OBJECT_MISSING,
        ErrorCode.CONFIGURATION_OUT_OF_SCOPE,
        ErrorCode.ORGANISATION_NOT_FOUND,
        ErrorCode.STAFF_NOT_FOUND,
    }
)
"""Refusals that mean "no such thing for you": another organisation's id answers like none."""


@dataclass(frozen=True)
class SkillChoice:
    """A skill as a checkbox of a form.

    Attributes:
        id: The skill's id; the input is ``skill-<id>``.
        label: The skill's label in the admin's language.
        code: The skill's code.
        checked: Whether the box is ticked.
        archived: Whether the skill is archived (kept where it is, never newly added).
    """

    id: int
    label: str
    code: str
    checked: bool
    archived: bool


def skill_label(skill: Skill, locale: str) -> str:
    """Return the label in ``locale``; Dutch when the skill has none in it."""
    return skill.labels.get(locale) or skill.labels["nl"]


def other_label(skill: Skill, locale: str) -> str:
    """Return the label in the other required language: English beside Dutch, else Dutch."""
    return skill.labels["nl" if locale == "en" else "en"]


def choices(skills: Iterable[Skill], checked: Iterable[int], locale: str) -> list[SkillChoice]:
    """Return ``skills`` as checkboxes, ticking ``checked``; skills without an id are left out."""
    ticked = frozenset(checked)
    return [
        SkillChoice(s.id, skill_label(s, locale), s.code, s.id in ticked, s.archived)
        for s in skills
        if s.id is not None
    ]


def chosen_ids(form: FormData) -> frozenset[int]:
    """Return the ticked skills (inputs named ``skill-<id>``); malformed names are ignored."""
    ids = (
        name.removeprefix(CHOICE_PREFIX) for name in form.values if name.startswith(CHOICE_PREFIX)
    )
    return frozenset(int(i) for i in ids if i.isdigit() and len(i) <= _MAX_ID_DIGITS)


def refuse(form: FormData, error: DomainError, locale: RequestLocale, choice_field: str) -> None:
    """Add the form error for a refusal of these screens; raise the page-level ones.

    A refused permission is an ``AccessDenied``; an object that is missing or another
    organisation's, and a person the viewer may not see, answer 404 like an unknown address.
    ``choice_field`` is the id of the group of checkboxes an error about the choice links to.

    Raises:
        AccessDenied: For ``not_permitted``.
        RejectedRequest: 404 for an unknown object, 409 for a legacy pool.
    """
    raise_refusal(error)
    _ = locale.gettext
    if error.field == "mode":
        raise RejectedRequest(409)
    if error.code is ErrorCode.SLUG_TAKEN and error.field == "skill_code":
        form.errors.append(FormError("skill_code", _("admin.skills.error.code_taken")))
    elif error.field in ("skill_ids", "guaranteed_skill_ids"):
        form.errors.append(FormError(choice_field, _("admin.skills.error.choice")))
    elif error.code is ErrorCode.CONFIGURATION_OUT_OF_SCOPE and error.field == "organisation_id":
        form.errors.append(FormError(choice_field, _("admin.skills.error.no_membership")))
    elif error.code in _UNKNOWN:
        raise RejectedRequest(404)
    else:
        form.refusal(error)


def raise_unknown(error: DomainError) -> NoReturn:
    """Turn a refusal of a page that has no form into its response, or re-raise it.

    Raises:
        AccessDenied: For ``not_permitted``.
        RejectedRequest: 404 for an unknown object, organisation or person.
        DomainError: ``error`` itself for anything else (the portal answers it).
    """
    raise_refusal(error)
    if error.code in _UNKNOWN:
        raise RejectedRequest(404)
    raise error
