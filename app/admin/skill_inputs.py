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


def ticked_ids(form: FormData) -> frozenset[int]:
    """Return the ticked skills (inputs named ``skill-<id>``); malformed names are ignored.

    Only to show the ticks of a form again; the ids a change acts on come from ``chosen_ids``.
    """
    ids = (
        name.removeprefix(CHOICE_PREFIX) for name in form.values if name.startswith(CHOICE_PREFIX)
    )
    return frozenset(int(i) for i in ids if i.isdigit() and len(i) <= _MAX_ID_DIGITS)


def chosen_ids(form: FormData, maximum: int) -> frozenset[int]:
    """Return the ticked skills a change acts on: at most ``maximum`` distinct ids.

    More than the organisation can have is no real form (a crafted POST); it is refused before
    anything is looked up.

    Raises:
        DomainError: ``invalid_configuration`` on ``skill_ids`` for more than ``maximum`` ids.
    """
    ids = ticked_ids(form)
    if len(ids) > maximum:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "skill_ids")
    return ids


def is_unknown(error: DomainError) -> bool:
    """Return whether a refusal means "no such thing for you" (answered 404).

    That is an unknown or another organisation's object, organisation or person, and a legacy
    pool, which has no page and no change here.
    """
    return error.code in _UNKNOWN or (
        error.code is ErrorCode.INVALID_CONFIGURATION and error.field == "mode"
    )


def refuse(form: FormData, error: DomainError, locale: RequestLocale, choice_field: str) -> None:
    """Add the form error for a refusal of these screens; raise the page-level ones.

    A refused permission is an ``AccessDenied``; an object that is missing or another
    organisation's, a legacy pool and a person the viewer may not see answer 404 like an unknown
    address. ``choice_field`` is the id of the group of checkboxes an error about the choice
    links to.

    Raises:
        AccessDenied: For ``not_permitted``.
        RejectedRequest: 404 for an unknown object.
    """
    _ = locale.gettext
    if error.code is ErrorCode.SLUG_TAKEN and error.field == "skill_code":
        form.errors.append(FormError("skill_code", _("admin.skills.error.code_taken")))
    elif error.field == "skill_limit":
        form.errors.append(FormError("skill_code", _("admin.skills.error.limit")))
    elif error.field == "pool_limit":
        form.errors.append(FormError("pool_name", _("admin.capacity.error.limit")))
    elif error.field in ("skill_ids", "guaranteed_skill_ids"):
        form.errors.append(FormError(choice_field, _("admin.skills.error.choice")))
    elif error.code is ErrorCode.CONFIGURATION_OUT_OF_SCOPE and error.field == "organisation_id":
        form.errors.append(FormError(choice_field, _("admin.skills.error.no_membership")))
    elif error.code is ErrorCode.NOT_PERMITTED or is_unknown(error):
        raise_unknown(error)
    else:
        form.refusal(error)


def raise_unknown(error: DomainError) -> NoReturn:
    """Turn a refusal of a page that has no form into its response, or re-raise it.

    Raises:
        AccessDenied: For ``not_permitted``.
        RejectedRequest: 404 for an unknown object, organisation or person, and a legacy pool.
        DomainError: ``error`` itself for anything else (the portal answers it).
    """
    raise_refusal(error)
    if is_unknown(error):
        raise RejectedRequest(404)
    raise error
