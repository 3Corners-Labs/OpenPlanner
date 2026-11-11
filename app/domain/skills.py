"""Skills: what a person can do, and what a flow or subject asks for (ADR 0121 points 5 and 6).

A skill is an organisation's own catalogue entry with a stable code and a label per locale.
A flow or one of its subjects *requires* skills; a staff member *holds* them. The rule is
ALL_OF: someone is eligible when nothing required is missing. Nothing here reads a label.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime

from app.domain.errors import DomainError, ErrorCode
from app.domain.routing import MAX_LABEL_LENGTH, REQUIRED_LOCALES, SUBJECT_CODE


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
    """

    id: int | None
    organisation_id: int
    code: str
    labels: Mapping[str, str]
    archived_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate the code (``skill_code``) and the labels (``skill_label``)."""
        if not SUBJECT_CODE.fullmatch(self.code):
            raise _invalid("skill_code")
        for locale in REQUIRED_LOCALES:
            if not self.labels.get(locale, "").strip():
                raise _invalid("skill_label")
        for text in self.labels.values():
            if len(text.strip()) > MAX_LABEL_LENGTH:
                raise _invalid("skill_label")

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


def effective_requirements(
    flow_skills: Collection[int], subject_flow_skills: Collection[int]
) -> frozenset[int]:
    """Return what a booking needs: the flow's skills plus the chosen subject's extra ones."""
    return frozenset(flow_skills) | frozenset(subject_flow_skills)


def usable_for_new_requirement(skill: Skill) -> bool:
    """Return whether ``skill`` may be newly required by a flow or subject (not archived)."""
    return not skill.archived
