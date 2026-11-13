"""Skills: what a person can do, and what a flow or subject asks for (ADR 0121 points 5 and 6).

A skill is an organisation's own catalogue entry with a stable code and a label per locale.
A flow or one of its subjects *requires* skills; a staff member *holds* them. The rule is
ALL_OF: someone is eligible when nothing required is missing. Nothing here reads a label.

A skill may carry a *contact-center code* (ADR 0121 step 4b): what the organisation's
contact-center flow tests for it. A dispatch sends the codes of the skills a booking requires
and nothing else; a required skill without a code is an incomplete configuration, never
dropped (``mapped_skill_codes``).
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime

from app.domain.errors import DomainError, ErrorCode, missing_object, outside_organisation
from app.domain.routing import SUBJECT_CODE, VARIABLE_VALUE, validate_labels

MAX_JOINED_CODES = 512
"""The longest value a contact-center flow variable takes (Webex Contact Center's ``flowSettings``
limit): the joined codes of one booking must fit in it. A limit of the platform, not a setting."""


def _invalid(name: str) -> DomainError:
    """Return the configuration error for ``name``."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, name)


@dataclass(frozen=True)
class Skill:
    """One skill of an organisation's catalogue.

    Attributes:
        id: Database id, ``None`` before it is stored.
        organisation_id: The organisation whose catalogue it belongs to.
        code: Stable code, the same grammar as a subject code.
        labels: The label per locale; ``nl`` and ``en`` are required, plain text.
        archived_at: When it was archived; an archived skill is kept but not newly required.
        contact_center_code: What the organisation's contact-center flow tests for the skill,
            the grammar of a routing value (ADR 0081); ``None`` while it is not mapped.
    """

    id: int | None
    organisation_id: int
    code: str
    labels: Mapping[str, str]
    archived_at: datetime | None = None
    contact_center_code: str | None = None

    def __post_init__(self) -> None:
        """Validate the code, the labels and the contact-center code (fields as named)."""
        if not SUBJECT_CODE.fullmatch(self.code):
            raise _invalid("skill_code")
        validate_labels(self.labels, "skill_label")
        code = self.contact_center_code
        if code is not None and not VARIABLE_VALUE.fullmatch(code):
            raise _invalid("contact_center_code")

    @property
    def archived(self) -> bool:
        """Return whether the skill is archived."""
        return self.archived_at is not None


def missing_skills(required: Collection[int], held: Collection[int]) -> frozenset[int]:
    """Return the required skill ids that ``held`` does not contain."""
    return frozenset(required) - frozenset(held)


def eligible(required: Collection[int], held: Collection[int]) -> bool:
    """Return whether ``held`` covers every required skill (ALL_OF); no requirement fits all."""
    return not missing_skills(required, held)


@dataclass(frozen=True)
class Eligible:
    """The person holds every required skill (or nothing is required)."""


@dataclass(frozen=True)
class Missing:
    """The person lacks at least one required skill.

    Attributes:
        skill_ids: The required skill ids the person does not hold; never empty.
    """

    skill_ids: frozenset[int]

    def __post_init__(self) -> None:
        """Refuse an empty set: lacking nothing is ``Eligible``."""
        if not self.skill_ids:
            raise ValueError("Missing without a skill")


Eligibility = Eligible | Missing
"""Whether someone may take a piece of work by skill (ADR 0121 point 5); rights are separate."""


def eligibility(required: Collection[int], held: Collection[int]) -> Eligibility:
    """Return whether ``held`` covers ``required`` (ALL_OF), naming what is missing if not.

    The one answer the queue, the claim and the start of treatment use. Nothing required is
    always ``Eligible``, so work without skills is open to everyone the matrix lets in.
    """
    missing = missing_skills(required, held)
    return Missing(missing) if missing else Eligible()


def unmapped_skills(required: Collection[int], skills: Mapping[int, Skill]) -> frozenset[int]:
    """Return the required skill ids without a contact-center code (an unknown id has none)."""
    return frozenset(
        skill_id
        for skill_id in required
        if (skill := skills.get(skill_id)) is None or skill.contact_center_code is None
    )


def mapped_skill_codes(required: Collection[int], skills: Mapping[int, Skill]) -> tuple[str, ...]:
    """Return the contact-center codes of the required skills, sorted; nothing else is sent.

    ``skills`` holds the organisation's skills by id. An unmapped requirement is an incomplete
    configuration and is never dropped.

    Raises:
        DomainError: ``integration_not_ready`` (field ``skill_codes``) when a required skill has
            no code, or when the joined codes exceed ``MAX_JOINED_CODES``.
    """
    if unmapped_skills(required, skills):
        raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "skill_codes")
    codes = tuple(sorted(str(skills[skill_id].contact_center_code) for skill_id in required))
    if len(",".join(codes).encode()) > MAX_JOINED_CODES:
        raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "skill_codes")
    return codes


def effective_requirements(
    flow_skills: Collection[int], subject_flow_skills: Collection[int]
) -> frozenset[int]:
    """Return what a booking needs: the flow's skills plus the chosen subject's extra ones."""
    return frozenset(flow_skills) | frozenset(subject_flow_skills)


@dataclass(frozen=True)
class SkillUsage:
    """Where a skill is used, so a screen can say "used by" and refuse a careless change.

    Attributes:
        staff: Staff members who hold it.
        pools: Capacity pools that guarantee it.
        flows: Flows whose current revision requires it, for the flow or for a subject.
    """

    staff: int = 0
    pools: int = 0
    flows: int = 0


def check_skill_save(
    skill: Skill,
    stored: Skill | None,
    same_code_exists: bool,
    *,
    same_contact_center_code_exists: bool = False,
) -> None:
    """Refuse saving ``skill`` given what is stored; the adapter loads the facts.

    ``stored`` is the skill with ``skill.id`` (``None`` for a new skill or an unknown id),
    ``same_code_exists`` whether another skill of the organisation has the code already and
    ``same_contact_center_code_exists`` whether another one has its contact-center code (a field
    ``contact_center_code`` refusal: two skills the platform could not tell apart).

    Raises:
        DomainError: ``configuration_object_missing`` (field ``skill_id``) for an id without a
            skill, ``configuration_out_of_scope`` (field ``organisation_id``) when the update
            names another organisation than the stored skill's, ``slug_taken`` (field
            ``skill_code``) for a duplicate code, ``slug_taken`` (field ``contact_center_code``)
            for a duplicate contact-center code.
    """
    if skill.id is not None:
        if stored is None:
            raise missing_object("skill_id")
        if stored.organisation_id != skill.organisation_id:
            raise outside_organisation("organisation_id")
    if same_code_exists:
        raise DomainError(ErrorCode.SLUG_TAKEN, "skill_code")
    if same_contact_center_code_exists:
        raise DomainError(ErrorCode.SLUG_TAKEN, "contact_center_code")


def check_skills_of(
    organisation_id: int,
    wanted: Collection[int],
    skills: Mapping[int, Skill],
    already: Collection[int],
    field: str,
) -> None:
    """Refuse a wanted skill id that is unknown, another organisation's, or archived and new.

    ``skills`` holds the loaded skills by id (an id that is absent does not exist), ``already``
    the ids the object has now: an archived skill may stay where it is, but is never newly
    added (ADR 0121 A28). Used by pools, flows and staff grants alike.

    Raises:
        DomainError: ``configuration_object_missing``, ``configuration_out_of_scope`` or
            ``invalid_configuration``, each with ``field``.
    """
    for skill_id in sorted(wanted):
        skill = skills.get(skill_id)
        if skill is None:
            raise missing_object(field)
        if skill.organisation_id != organisation_id:
            raise outside_organisation(field)
        if skill.archived and skill_id not in already:
            raise _invalid(field)


def check_staff_grant(
    organisation_id: int,
    member_of: Collection[int],
    skills: Mapping[int, Skill],
    wanted: Collection[int],
    held_in_organisation: Collection[int],
) -> None:
    """Refuse a staff member's new skill set for one organisation.

    ``member_of`` holds the organisations the person has a membership in now, ``skills`` the
    loaded skills by id, ``wanted`` the whole new set for ``organisation_id`` and
    ``held_in_organisation`` what the person holds of that organisation today. Removing is
    always allowed; adding needs a membership, and a skill that is added must be the
    organisation's own and not archived.

    Raises:
        DomainError: ``configuration_out_of_scope`` (field ``organisation_id``) for an addition
            without a membership; for the skills ``configuration_object_missing``,
            ``configuration_out_of_scope`` or ``invalid_configuration`` (field ``skill_ids``).
    """
    if frozenset(wanted) - frozenset(held_in_organisation) and organisation_id not in member_of:
        raise outside_organisation("organisation_id")
    check_skills_of(organisation_id, wanted, skills, held_in_organisation, "skill_ids")
