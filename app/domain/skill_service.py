"""Skill use cases: the organisation's catalogue and the skills of its staff (ADR 0121).

A skill is the organisation's own catalogue entry (code, a label per locale). It grants no
rights and no access to data: it only decides which requests a member of staff may pick up.
Every operation asks the matrix first (``AdminService.scoped``) and every change runs in one
locked transaction with its audit event, carrying skill codes and the staff member's number,
never a name or a label. Reading the catalogue is ``organisation.view``; every change, and the
pages that edit one skill, are ``skills.manage``.

    catalogue      organisation.view   the organisation's skills with where each is used
    skill          skills.manage       one skill, not archived, for its edit and archive pages
    create         skills.manage       code (fixed from then on) and a label per locale; at most
                                       ``max_skills_per_organisation`` not archived
    relabel        skills.manage       the labels of a skill; says whether they changed
    archive        skills.manage       no new requirement or grant; what has it keeps it
    staff_skills   staff.view          the skills a person holds, per organisation they belong to
    grant          skills.manage       replace the skills a person holds in one organisation
"""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime

from app.domain.admin import AdminService
from app.domain.authorization import Permission, Principal, Scope, scope_for
from app.domain.errors import DomainError, ErrorCode, missing_object
from app.domain.organisations import organisation_by_slug, own_object
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Organisation
from app.domain.skills import Skill, SkillUsage
from app.domain.tunable import value_of

_PERMISSION = Permission.SKILLS_MANAGE
CREATED = "skill.created"
CHANGED = "skill.changed"
ARCHIVED = "skill.archived"
GRANTS_CHANGED = "staff_skills.changed"
"""Details of the audit events (action ``skills.manage``); the labels are in the audit page."""
LIMIT_FIELD = "skill_limit"
"""The field of the refusal when the organisation has as many skills as the platform allows."""


@dataclass(frozen=True)
class SkillLine:
    """One skill with where it is used, for the catalogue."""

    skill: Skill
    usage: SkillUsage


@dataclass(frozen=True)
class SkillPage:
    """One skill of an organisation with its use, for the pages about that skill."""

    organisation: Organisation
    line: SkillLine


@dataclass(frozen=True)
class SkillCatalogue:
    """An organisation's skills, the archived ones or those in use.

    Attributes:
        organisation: The organisation.
        lines: The skills, by code, with their use.
        archived: Whether the archived skills are listed instead of the others.
        editable: Whether the viewer may change them (``skills.manage`` in this organisation).
        can_add: Whether another skill fits under the platform's limit.
        limit: That limit: skills not archived per organisation.
    """

    organisation: Organisation
    lines: tuple[SkillLine, ...]
    archived: bool
    editable: bool
    can_add: bool
    limit: int


@dataclass(frozen=True)
class PersonSkills:
    """What a person holds in one organisation they belong to.

    Attributes:
        organisation: The organisation.
        choices: The skills that can be shown as a choice: those not archived, and an archived
            one the person still holds, so it can be taken away.
        held: The ids of the skills the person holds here.
        editable: Whether the viewer may change them (``skills.manage`` in this organisation).
    """

    organisation: Organisation
    choices: tuple[Skill, ...]
    held: frozenset[int]
    editable: bool


def labels_of(label_nl: str, label_en: str) -> Mapping[str, str]:
    """Return the labels of a skill as the domain keeps them, surrounding spaces removed."""
    return {"nl": label_nl.strip(), "en": label_en.strip()}


async def all_skills(tx: Transaction, scope: Scope, organisation_id: int) -> list[Skill]:
    """Return every skill of the organisation inside ``scope``, archived ones too."""
    every = await tx.list_skills(scope, organisation_id)
    return every + await tx.list_skills(scope, organisation_id, archived=True)


async def organisation_skills(
    tx: Transaction, scope: Scope, organisation_id: int, *, held: Collection[int] = ()
) -> list[Skill]:
    """Return the skills not archived, and those archived among ``held``, by code."""
    active = await tx.list_skills(scope, organisation_id)
    kept = [s for s in await tx.list_skills(scope, organisation_id, archived=True) if s.id in held]
    return sorted([*active, *kept], key=lambda skill: skill.code)


def can_manage(principal: Principal, slug: str) -> bool:
    """Return whether ``principal`` holds ``skills.manage`` in organisation ``slug``."""
    return scope_for(principal, _PERMISSION).contains(slug, None)


class SkillService:
    """Application service for the skill catalogue and the skills of staff.

    Args:
        uow: Opens database transactions.
        clock: Current time, for audit events and grants.
        admin: Authorises changes and audits refusals.
        max_skills: The platform's limit of skills not archived per organisation (class C,
            ``max_skills_per_organisation``), or the live setting that holds it.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        max_skills: int | Callable[[], int],
    ) -> None:
        """Store the collaborators and the limit."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._max = max_skills

    @property
    def max_skills(self) -> int:
        """The platform's limit of skills not archived per organisation."""
        return value_of(self._max)

    async def _record(
        self,
        tx: Transaction,
        principal: Principal,
        slug: str,
        detail: str,
        at: datetime | None = None,
    ) -> None:
        """Audit a change of the organisation in the transaction that made it."""
        await self._admin.record_change(
            tx, at or self._clock.now(), principal, _PERMISSION, slug, None, detail
        )

    async def catalogue(
        self, principal: Principal, slug: str, *, archived: bool = False
    ) -> SkillCatalogue:
        """Return the organisation's skills (the archived ones on request) with their use.

        Raises:
            DomainError: ``not_permitted`` (``organisation.view``) or ``organisation_not_found``.
        """
        scope = await self._admin.scoped(principal, Permission.ORGANISATION_VIEW, slug)
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            lines = [
                SkillLine(skill, await tx.skill_usage(scope, skill.id or 0))
                for skill in await tx.list_skills(scope, organisation.id, archived=archived)
            ]
            active = (
                len(lines) if not archived else len(await tx.list_skills(scope, organisation.id))
            )
        return SkillCatalogue(
            organisation,
            tuple(lines),
            archived,
            can_manage(principal, slug),
            active < self.max_skills,
            self.max_skills,
        )

    async def skill(self, principal: Principal, slug: str, skill_id: int) -> SkillPage:
        """Return one skill of the organisation, not archived, with its use.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``configuration_object_missing`` (also for another organisation's skill and for
                an archived one, which is not edited).
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            skill = await _own_active_skill(tx, scope, organisation, skill_id)
            usage = await tx.skill_usage(scope, skill_id)
        return SkillPage(organisation, SkillLine(skill, usage))

    async def create(
        self, principal: Principal, slug: str, code: str, label_nl: str, label_en: str
    ) -> Skill:
        """Add a skill to the organisation's catalogue.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``, ``invalid_configuration``
                (fields ``skill_code``, ``skill_label``, ``skill_limit`` when the organisation
                has as many skills not archived as the platform allows) or ``slug_taken``
                (``skill_code``).
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            skill = Skill(None, organisation.id, code.strip(), labels_of(label_nl, label_en))
            if len(await tx.list_skills(scope, organisation.id)) >= self.max_skills:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, LIMIT_FIELD)
            stored = await tx.save_skill(scope, skill)
            await self._record(tx, principal, slug, f"{CREATED},skill:{stored.code}")
        return stored

    async def relabel(
        self, principal: Principal, slug: str, skill_id: int, label_nl: str, label_en: str
    ) -> bool:
        """Change the labels of a skill, not archived; its code stays. Return whether they changed.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``,
                ``configuration_object_missing`` (also for an archived skill) or
                ``invalid_configuration`` (``skill_label``).
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            current = await _own_active_skill(tx, scope, organisation, skill_id)
            changed = Skill(
                current.id,
                current.organisation_id,
                current.code,
                labels_of(label_nl, label_en),
                current.archived_at,
            )
            if changed.labels == current.labels:
                return False
            await tx.save_skill(scope, changed)
            await self._record(tx, principal, slug, f"{CHANGED},skill:{current.code}")
        return True

    async def archive(self, principal: Principal, slug: str, skill_id: int) -> None:
        """Archive a skill: nothing newly requires or grants it, what has it keeps it.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``configuration_object_missing``.
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            skill = own_object(await tx.get_skill(scope, skill_id), organisation, "skill_id")
            if skill.archived:
                return
            now = self._clock.now()
            await tx.archive_skill(scope, skill_id, now)
            await self._record(tx, principal, slug, f"{ARCHIVED},skill:{skill.code}", now)

    async def staff_skills(self, principal: Principal, member_id: int) -> list[PersonSkills]:
        """Return the skills of a person per organisation they belong to inside the viewer's reach.

        The person must be visible to the viewer (``staff.view``); whether a section is
        editable follows from ``skills.manage`` in that organisation.

        Raises:
            DomainError: ``staff_not_found`` for a person the viewer may not see.
        """
        member = await self._admin.staff_member(principal, member_id)
        if member is None:
            raise DomainError(ErrorCode.STAFF_NOT_FOUND)
        seen = scope_for(principal, Permission.STAFF_VIEW)
        sections = []
        async with self._uow.read() as tx:
            held = await tx.list_staff_skills(seen, member_id)
            for slug in sorted(member.organisations):
                organisation = await tx.get_organisation(slug)
                if organisation is None:
                    continue
                choices = await organisation_skills(tx, seen, organisation.id, held=held)
                ids = {skill.id for skill in choices}
                sections.append(
                    PersonSkills(
                        organisation,
                        tuple(choices),
                        frozenset(held & ids),
                        can_manage(principal, slug),
                    )
                )
        return sections

    async def grant(
        self, principal: Principal, slug: str, member_id: int, skill_ids: Collection[int]
    ) -> bool:
        """Replace the skills a person holds in the organisation; return whether it changed.

        What is audited is what the store really replaced: a grant that outlived the person's
        membership counts as held, so taking it away is a change too.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``, ``staff_not_found``,
                ``configuration_out_of_scope`` (``organisation_id``: no membership) or, for
                ``skill_ids``, ``configuration_object_missing``, ``configuration_out_of_scope``
                and ``invalid_configuration``.
        """
        scope = await self._admin.scoped(principal, _PERMISSION, slug)
        if await self._admin.staff_member(principal, member_id) is None:
            raise DomainError(ErrorCode.STAFF_NOT_FOUND)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            codes = {s.id: s.code for s in await all_skills(tx, scope, organisation.id)}
            actor = await tx.get_staff(principal.subject)
            now = self._clock.now()
            added, removed = await tx.set_staff_skills(
                scope,
                member_id,
                organisation.id,
                skill_ids,
                granted_by=actor.id if actor else None,
                at=now,
            )
            if not (added or removed):
                return False
            changes = [f"skill.add:{codes[i]}" for i in sorted(added)]
            changes += [f"skill.remove:{codes[i]}" for i in sorted(removed)]
            detail = ",".join([GRANTS_CHANGED, f"staff:{member_id}", *changes])
            await self._record(tx, principal, slug, detail, now)
        return True


async def _own_active_skill(
    tx: Transaction, scope: Scope, organisation: Organisation, skill_id: int
) -> Skill:
    """Return the skill if it is the organisation's own and not archived (an archived one is kept).

    Another organisation's skill answers like a missing one, so ids tell nothing (A05); so does
    an archived one, which has no edit or archive page.
    """
    skill = own_object(await tx.get_skill(scope, skill_id), organisation, "skill_id")
    if skill.archived:
        raise missing_object("skill_id")
    return skill
