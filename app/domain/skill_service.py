"""Skill use cases: the organisation's catalogue and the skills of its staff (ADR 0121).

A skill is the organisation's own catalogue entry (code, a label per locale). It grants no
rights and no access to data: it only decides which requests a member of staff may pick up.
Every operation asks ``AdminService.authorize`` first and every change runs in one locked
transaction with its audit event, carrying skill codes and the staff member's number, never a
name or a label.

    catalogue      skills.manage   the organisation's skills with where each is used
    skill          skills.manage   one skill of the organisation
    create         skills.manage   code (fixed from then on) and a label per locale
    relabel        skills.manage   the labels of a skill
    archive        skills.manage   no new requirement or grant; what has it keeps it
    staff_skills   staff.view      the skills a person holds, per organisation they belong to
    grant          skills.manage   replace the skills a person holds in one organisation
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from app.domain.admin import AdminService, record_change, require
from app.domain.authorization import Permission, Principal, Scope, scope_for
from app.domain.errors import DomainError, ErrorCode, missing_object
from app.domain.organisations import organisation_by_slug
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Organisation
from app.domain.skills import Skill, SkillUsage

_PERMISSION = Permission.SKILLS_MANAGE
CREATED = "skill.created"
CHANGED = "skill.changed"
ARCHIVED = "skill.archived"
GRANTS_CHANGED = "staff_skills.changed"
"""Details of the audit events (action ``skills.manage``); the labels are in the audit page."""


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
    """An organisation's skills, the archived ones or those in use."""

    organisation: Organisation
    lines: tuple[SkillLine, ...]
    archived: bool


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


async def organisation_skills(
    tx: Transaction, scope: Scope, organisation_id: int, *, held: Collection[int] = ()
) -> list[Skill]:
    """Return the skills not archived, and those archived among ``held``, by code."""
    active = await tx.list_skills(scope, organisation_id)
    kept = [s for s in await tx.list_skills(scope, organisation_id, archived=True) if s.id in held]
    return sorted([*active, *kept], key=lambda skill: skill.code)


class SkillService:
    """Application service for the skill catalogue and the skills of staff.

    Args:
        uow: Opens database transactions.
        clock: Current time, for audit events and grants.
        admin: Authorises changes and audits refusals.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, admin: AdminService) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin

    async def catalogue(
        self, principal: Principal, slug: str, *, archived: bool = False
    ) -> SkillCatalogue:
        """Return the organisation's skills (the archived ones on request) with their use.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        await self._authorize(principal, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            lines = [
                SkillLine(skill, await tx.skill_usage(scope, skill.id or 0))
                for skill in await tx.list_skills(scope, organisation.id, archived=archived)
            ]
        return SkillCatalogue(organisation, tuple(lines), archived)

    async def skill(self, principal: Principal, slug: str, skill_id: int) -> SkillPage:
        """Return one skill of the organisation with its use.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``configuration_object_missing`` (also for another organisation's skill).
        """
        await self._authorize(principal, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            skill = await _own_skill(tx, scope, organisation, skill_id)
            usage = await tx.skill_usage(scope, skill_id)
        return SkillPage(organisation, SkillLine(skill, usage))

    async def create(
        self, principal: Principal, slug: str, code: str, label_nl: str, label_en: str
    ) -> Skill:
        """Add a skill to the organisation's catalogue.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``, ``invalid_configuration``
                (fields ``skill_code``, ``skill_label``) or ``slug_taken`` (``skill_code``).
        """
        await self._authorize(principal, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            skill = Skill(None, organisation.id, code.strip(), labels_of(label_nl, label_en))
            stored = await tx.save_skill(scope, skill)
            await record_change(
                tx,
                self._clock.now(),
                principal,
                _PERMISSION,
                slug,
                None,
                f"{CREATED},skill:{stored.code}",
            )
        return stored

    async def relabel(
        self, principal: Principal, slug: str, skill_id: int, label_nl: str, label_en: str
    ) -> Skill:
        """Change the labels of a skill; its code stays.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``,
                ``configuration_object_missing`` or ``invalid_configuration`` (``skill_label``).
        """
        await self._authorize(principal, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            current = await _own_skill(tx, scope, organisation, skill_id)
            changed = Skill(
                current.id,
                current.organisation_id,
                current.code,
                labels_of(label_nl, label_en),
                current.archived_at,
            )
            if changed.labels == current.labels:
                return current
            stored = await tx.save_skill(scope, changed)
            await record_change(
                tx,
                self._clock.now(),
                principal,
                _PERMISSION,
                slug,
                None,
                f"{CHANGED},skill:{stored.code}",
            )
        return stored

    async def archive(self, principal: Principal, slug: str, skill_id: int) -> None:
        """Archive a skill: nothing newly requires or grants it, what has it keeps it.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``configuration_object_missing``.
        """
        await self._authorize(principal, slug)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            skill = await _own_skill(tx, scope, organisation, skill_id)
            if skill.archived:
                return
            now = self._clock.now()
            await tx.archive_skill(scope, skill_id, now)
            await record_change(
                tx, now, principal, _PERMISSION, slug, None, f"{ARCHIVED},skill:{skill.code}"
            )

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
        manage = scope_for(principal, _PERMISSION)
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
                        manage.contains(slug, None),
                    )
                )
        return sections

    async def grant(
        self, principal: Principal, slug: str, member_id: int, skill_ids: Collection[int]
    ) -> bool:
        """Replace the skills a person holds in the organisation; return whether it changed.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``, ``staff_not_found``,
                ``configuration_out_of_scope`` (``organisation_id``: no membership) or, for
                ``skill_ids``, ``configuration_object_missing``, ``configuration_out_of_scope``
                and ``invalid_configuration``.
        """
        await self._authorize(principal, slug)
        if await self._admin.staff_member(principal, member_id) is None:
            raise DomainError(ErrorCode.STAFF_NOT_FOUND)
        scope = scope_for(principal, _PERMISSION)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            every = await tx.list_skills(scope, organisation.id)
            every += await tx.list_skills(scope, organisation.id, archived=True)
            codes = {skill.id: skill.code for skill in every}
            before = frozenset(await tx.list_staff_skills(scope, member_id)) & codes.keys()
            actor = await tx.get_staff(principal.subject)
            now = self._clock.now()
            await tx.set_staff_skills(
                scope,
                member_id,
                organisation.id,
                skill_ids,
                granted_by=actor.id if actor else None,
                at=now,
            )
            after = frozenset(await tx.list_staff_skills(scope, member_id)) & codes.keys()
            if after == before:
                return False
            changes = [f"skill.add:{codes[i]}" for i in sorted(after - before)]
            changes += [f"skill.remove:{codes[i]}" for i in sorted(before - after)]
            await record_change(
                tx,
                now,
                principal,
                _PERMISSION,
                slug,
                None,
                ",".join([GRANTS_CHANGED, f"staff:{member_id}", *changes]),
            )
        return True

    async def _authorize(self, principal: Principal, slug: str) -> None:
        """Raise ``not_permitted`` unless the matrix grants ``skills.manage`` on ``slug``."""
        require(await self._admin.authorize(principal, _PERMISSION, organisation_slug=slug))


async def _own_skill(
    tx: Transaction, scope: Scope, organisation: Organisation, skill_id: int
) -> Skill:
    """Return the skill if it exists inside ``scope`` and is the organisation's own.

    Another organisation's skill answers like a missing one, so ids tell nothing (A05).
    """
    skill = await tx.get_skill(scope, skill_id)
    if skill is None or skill.organisation_id != organisation.id:
        raise missing_object("skill_id")
    return skill
