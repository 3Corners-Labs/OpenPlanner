"""Inputs shared by the screens of skills, capacity pools and staff skill grants (ADR 0121).

A skill choice is a checkbox named ``skill-<id>`` (one value per name, like the holiday
calendars). The same helpers show the label of a skill in the admin's language, turn
the ticked boxes into ids and map the domain's refusals of those screens to form errors.
"""

import re
from collections.abc import Callable, Iterable
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

SKILL_LIMIT_NOTICE = "skills-limit-notice"
"""Id of the notice that the limit of skills is reached; a refused create links to it (the add
form is not shown then, so no input can carry the error)."""

POOL_LIMIT_NOTICE = "pools-limit-notice"
"""Id of the notice that the limit of pools is reached; see ``SKILL_LIMIT_NOTICE``."""

ID_PATTERN = re.compile(r"[0-9]{1,9}")
"""A skill id in an input name: ASCII digits only (``str.isdigit`` also accepts ``²``)."""

_ARCHIVED_FACTOR = 2
"""A form offers the skills not archived and the archived ones still held: twice the limit."""

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


def skill_name(skill: Skill, locale: str) -> str:
    """Return how every list names a skill: its label in ``locale`` and its code in parentheses.

    The one home of the form (the checkbox lists show the same two parts): two skills may share
    a label, and the code tells them apart.
    """
    return f"{skill_label(skill, locale)} ({skill.code})"


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
    An id is one to nine ASCII digits; ``skill-²``, ``skill--1``, an empty or a longer one is
    ignored like any other name.
    """
    ids = (
        name.removeprefix(CHOICE_PREFIX) for name in form.values if name.startswith(CHOICE_PREFIX)
    )
    return frozenset(int(i) for i in ids if ID_PATTERN.fullmatch(i))


def chosen_ids(form: FormData, max_skills: int) -> frozenset[int]:
    """Return the ticked skills a change acts on: at most twice ``max_skills`` distinct ids.

    ``max_skills`` is the limit of skills not archived. A form also offers the archived skills
    a pool or person still holds, so the cap is twice the limit: it needs no query before the
    parse and no real form is near it. More is a crafted POST, refused before anything is looked
    up.

    Raises:
        DomainError: ``invalid_configuration`` on ``skill_ids`` for more ids than the cap.
    """
    ids = ticked_ids(form)
    if len(ids) > _ARCHIVED_FACTOR * max_skills:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "skill_ids")
    return ids


@dataclass(frozen=True)
class Rule:
    """One refusal of the domain and the form error it becomes.

    Attributes:
        field: The refusal's field, ``None`` for any.
        key: The catalogue key of the message.
        target: The id of the input the error links to: ``None`` for a page-level error,
            ``CHOICE`` for the group of checkboxes of the screen.
        code: The refusal's code, ``None`` for any.
    """

    field: str | None
    key: str
    target: str | None
    code: ErrorCode | None = None

    def matches(self, error: DomainError) -> bool:
        """Return whether ``error`` is the refusal this rule is for."""
        return (self.field is None or error.field == self.field) and (
            self.code is None or error.code is self.code
        )


CHOICE = ":choice"
"""The target of a rule that links to the screen's group of checkboxes (``refuse``'s argument)."""


@dataclass(frozen=True)
class RefusalTable:
    """How the refusals of one set of screens become form errors, in order of precedence.

    Attributes:
        rules: The refusals that are errors of an input or of the page; the first match wins.
        unknown: Whether a refusal means "no such thing for you" (answered 404), for the
            refusals no rule took.
    """

    rules: tuple[Rule, ...]
    unknown: Callable[[DomainError], bool]


def is_unknown(error: DomainError) -> bool:
    """Return whether a refusal means "no such thing for you" (answered 404).

    That is an unknown or another organisation's object, organisation or person, and a legacy
    pool, which has no page and no change here.
    """
    return error.code in _UNKNOWN or (
        error.code is ErrorCode.INVALID_CONFIGURATION and error.field == "mode"
    )


SKILLS = RefusalTable(
    (
        Rule("skill_code", "admin.skills.error.code_taken", "skill_code", ErrorCode.SLUG_TAKEN),
        Rule(
            "contact_center_code",
            "admin.skills.error.contact_center_code_taken",
            "contact_center_code",
            ErrorCode.SLUG_TAKEN,
        ),
        Rule(
            "contact_center_code",
            "admin.skills.error.contact_center_code",
            "contact_center_code",
            ErrorCode.INVALID_CONFIGURATION,
        ),
        Rule("pool_code", "admin.capacity.error.code_taken", "pool_code", ErrorCode.SLUG_TAKEN),
        Rule("pool_name", "admin.capacity.error.name_taken", "pool_name", ErrorCode.SLUG_TAKEN),
        Rule("skill_limit", "admin.skills.error.limit", SKILL_LIMIT_NOTICE),
        Rule("pool_limit", "admin.capacity.error.limit", POOL_LIMIT_NOTICE),
        Rule(
            "guaranteed_skill_ids",
            "admin.capacity.error.skill_in_use",
            CHOICE,
            ErrorCode.CONFIGURATION_OBJECT_IN_USE,
        ),
        Rule("skill_ids", "admin.skills.error.choice", CHOICE),
        Rule("guaranteed_skill_ids", "admin.skills.error.choice", CHOICE),
        Rule(
            "organisation_id",
            "admin.skills.error.no_membership",
            CHOICE,
            ErrorCode.CONFIGURATION_OUT_OF_SCOPE,
        ),
    ),
    is_unknown,
)
"""The refusals of the screens of skills, capacity pools and a person's skills."""


def refuse(
    form: FormData,
    error: DomainError,
    locale: RequestLocale,
    choice_field: str,
    table: RefusalTable = SKILLS,
) -> None:
    """Add the form error for a refusal of a set of screens; raise the page-level ones.

    A refused permission is an ``AccessDenied``; an object that is missing or another
    organisation's, a legacy pool and a person the viewer may not see answer 404 like an unknown
    address (``table.unknown``). ``choice_field`` is the id of the group of checkboxes an error
    about the choice links to. What no rule of ``table`` takes is the form's generic message.

    Raises:
        AccessDenied: For ``not_permitted``.
        RejectedRequest: 404 for an unknown object.
    """
    raise_refusal(error)
    for rule in table.rules:
        if rule.matches(error):
            target = choice_field if rule.target == CHOICE else rule.target
            form.errors.append(FormError(target, locale.gettext(rule.key)))
            return
    if table.unknown(error):
        raise_unknown(error, table)
    form.refusal(error)


def raise_unknown(error: DomainError, table: RefusalTable = SKILLS) -> NoReturn:
    """Turn a refusal of a page that has no form into its response, or re-raise it.

    Raises:
        AccessDenied: For ``not_permitted``.
        RejectedRequest: 404 for an unknown object, organisation or person, and a legacy pool
            (what ``table.unknown`` says).
        DomainError: ``error`` itself for anything else (the portal answers it).
    """
    raise_refusal(error)
    if table.unknown(error):
        raise RejectedRequest(404)
    raise error
