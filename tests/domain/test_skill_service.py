"""SkillService (ADR 0121): authorisation, audit details, limits, archived skills and grants."""

import pytest

from app.domain import audit
from app.domain.admin import AdminService
from app.domain.authorization import Principal
from app.domain.errors import DomainError, ErrorCode
from app.domain.skill_service import SkillService
from app.domain.staff import Membership, StaffMember
from tests.fakes import clock as fake_clock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import AUDITOR, GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, two_organisations
from tests.support.domain import NOW


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


def make(uow: InMemoryUnitOfWork, limit: int = 3) -> SkillService:
    clock = fake_clock.FixedClock(NOW)
    return SkillService(uow, clock, AdminService(uow, clock), lambda: limit)


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> SkillService:
    return make(uow)


def details(uow: InMemoryUnitOfWork) -> list[str]:
    return [e.detail for e in uow.state.audit if e.outcome is audit.Outcome.SUCCESS]


def denials(uow: InMemoryUnitOfWork) -> list[audit.AuditEvent]:
    return [e for e in uow.state.audit if e.outcome is audit.Outcome.DENIED]


async def person(uow: InMemoryUnitOfWork, subject: str, *slugs: str) -> int:
    async with uow.locked() as tx:
        await tx.save_staff(
            StaffMember(
                subject,
                "Staff",
                frozenset(),
                frozenset({Membership("org_admin", slug) for slug in slugs}),
                NOW,
                NOW,
                NOW,
            )
        )
        member = await tx.get_staff(subject)
    assert member is not None
    assert member.id is not None
    return member.id


async def test_a_refusal_is_audited_and_changes_nothing(
    service: SkillService, uow: InMemoryUnitOfWork
) -> None:
    for who in (GROUP_ADMIN, OUTSIDER, AUDITOR):
        with pytest.raises(DomainError) as refused:
            await service.create(who, "demo", "intake", "Intake", "Intake")
        assert refused.value.code is ErrorCode.NOT_PERMITTED
    with pytest.raises(DomainError):
        await service.catalogue(GROUP_ADMIN, "demo")

    assert len(denials(uow)) == 4
    assert details(uow) == []
    assert not uow.state.skills


async def test_the_catalogue_is_read_under_the_organisation_view_right(
    service: SkillService,
) -> None:
    await service.create(ORG_ADMIN, "demo", "intake", "Intake", "Intake")

    as_auditor = await service.catalogue(AUDITOR, "demo")
    as_admin = await service.catalogue(ORG_ADMIN, "demo")

    assert [line.skill.code for line in as_auditor.lines] == ["intake"]
    assert (as_auditor.editable, as_admin.editable) == (False, True)
    assert (as_admin.can_add, as_admin.limit) == (True, 3)
    with pytest.raises(DomainError):  # the page of one skill stays a manage right
        await service.skill(AUDITOR, "demo", 1)


async def test_each_change_is_audited_with_codes_only_and_a_same_save_with_nothing(
    service: SkillService, uow: InMemoryUnitOfWork
) -> None:
    skill = await service.create(ORG_ADMIN, "demo", " intake ", " Intake ", "Intake (en)")
    assert skill.id is not None
    assert await service.relabel(ORG_ADMIN, "demo", skill.id, "Intake", "Intake (en)") is False
    assert await service.relabel(ORG_ADMIN, "demo", skill.id, "Aanmelding", "Intake (en)") is True
    await service.archive(ORG_ADMIN, "demo", skill.id)
    await service.archive(ORG_ADMIN, "demo", skill.id)  # already archived: nothing again

    assert details(uow) == [
        "skill.created,skill:intake",
        "skill.changed,skill:intake",
        "skill.archived,skill:intake",
    ]
    assert all("Aanmelding" not in d for d in details(uow))


async def test_an_archived_skill_has_no_page_and_cannot_be_relabelled(
    service: SkillService,
) -> None:
    skill = await service.create(ORG_ADMIN, "demo", "intake", "Intake", "Intake")
    assert skill.id is not None
    await service.archive(ORG_ADMIN, "demo", skill.id)

    for call in (
        service.skill(ORG_ADMIN, "demo", skill.id),
        service.relabel(ORG_ADMIN, "demo", skill.id, "x", "x"),
    ):
        with pytest.raises(DomainError) as refused:
            await call
        assert refused.value.code is ErrorCode.CONFIGURATION_OBJECT_MISSING


async def test_another_organisations_skill_answers_like_a_missing_one(
    service: SkillService,
) -> None:
    theirs = await service.create(SUPER, "andere", "theirs", "Theirs", "Theirs")
    assert theirs.id is not None

    for call in (
        service.skill(ORG_ADMIN, "demo", theirs.id),
        service.relabel(ORG_ADMIN, "demo", theirs.id, "x", "x"),
        service.archive(ORG_ADMIN, "demo", theirs.id),
    ):
        with pytest.raises(DomainError) as refused:
            await call
        assert refused.value.code is ErrorCode.CONFIGURATION_OBJECT_MISSING


async def test_the_limit_counts_skills_not_archived(service: SkillService) -> None:
    ids = [
        (await service.create(ORG_ADMIN, "demo", code, code.title(), code.title())).id
        for code in ("one", "two", "three")
    ]
    full = await service.catalogue(ORG_ADMIN, "demo")

    with pytest.raises(DomainError) as refused:
        await service.create(ORG_ADMIN, "demo", "four", "Four", "Four")
    await service.archive(ORG_ADMIN, "demo", ids[0] or 0)
    await service.create(ORG_ADMIN, "demo", "four", "Four", "Four")
    other = await service.create(SUPER, "andere", "one", "One", "One")  # its own catalogue

    assert (refused.value.code, refused.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "skill_limit",
    )
    assert full.can_add is False
    assert other.code == "one"
    assert (await service.catalogue(ORG_ADMIN, "demo", archived=True)).can_add is False


async def test_grants_add_and_remove_with_the_persons_number_in_the_audit(
    service: SkillService, uow: InMemoryUnitOfWork
) -> None:
    member = await person(uow, "sub-1", "demo")
    first = await service.create(ORG_ADMIN, "demo", "first", "Eerste", "First")
    second = await service.create(ORG_ADMIN, "demo", "second", "Tweede", "Second")
    assert first.id is not None
    assert second.id is not None

    assert await service.grant(ORG_ADMIN, "demo", member, {first.id}) is True
    assert await service.grant(ORG_ADMIN, "demo", member, {first.id}) is False
    assert await service.grant(ORG_ADMIN, "demo", member, {second.id}) is True

    assert details(uow)[2:] == [
        f"staff_skills.changed,staff:{member},skill.add:first",
        f"staff_skills.changed,staff:{member},skill.add:second,skill.remove:first",
    ]


async def test_a_grant_is_scoped_to_one_organisation_and_needs_a_visible_person(
    service: SkillService, uow: InMemoryUnitOfWork
) -> None:
    both = await person(uow, "sub-both", "demo", "andere")
    only_other = await person(uow, "sub-other", "andere")
    ours = await service.create(ORG_ADMIN, "demo", "ours", "Onze", "Ours")
    theirs = await service.create(SUPER, "andere", "theirs", "Hun", "Theirs")
    assert ours.id is not None
    assert theirs.id is not None
    await service.grant(SUPER, "andere", both, {theirs.id})

    await service.grant(ORG_ADMIN, "demo", both, {ours.id})  # the other organisation stays
    with pytest.raises(DomainError) as unseen:
        await service.grant(ORG_ADMIN, "demo", only_other, set())
    with pytest.raises(DomainError) as elsewhere:
        await service.grant(ORG_ADMIN, "demo", both, {theirs.id})
    with pytest.raises(DomainError) as no_member:
        await service.grant(SUPER, "demo", only_other, {ours.id})

    sections = {s.organisation.slug: s.held for s in await service.staff_skills(SUPER, both)}
    assert sections == {"demo": {ours.id}, "andere": {theirs.id}}
    assert unseen.value.code is ErrorCode.STAFF_NOT_FOUND
    assert elsewhere.value.code in (
        ErrorCode.CONFIGURATION_OBJECT_MISSING,
        ErrorCode.CONFIGURATION_OUT_OF_SCOPE,
    )
    assert (no_member.value.code, no_member.value.field) == (
        ErrorCode.CONFIGURATION_OUT_OF_SCOPE,
        "organisation_id",
    )


async def test_taking_away_a_grant_that_outlived_the_membership_is_audited(
    service: SkillService, uow: InMemoryUnitOfWork
) -> None:
    member = await person(uow, "sub-1", "demo", "andere")
    skill = await service.create(ORG_ADMIN, "demo", "old", "Oud", "Old")
    assert skill.id is not None
    await service.grant(ORG_ADMIN, "demo", member, {skill.id})
    await person(uow, "sub-1", "andere")  # the person leaves demo; the grant row stays

    changed = await service.grant(SUPER, "demo", member, set())

    assert changed is True
    assert details(uow)[-1] == f"staff_skills.changed,staff:{member},skill.remove:old"


async def test_the_person_of_another_organisation_is_unknown_to_the_staff_page(
    service: SkillService, uow: InMemoryUnitOfWork
) -> None:
    other = await person(uow, "sub-other", "andere")

    with pytest.raises(DomainError) as unseen:
        await service.staff_skills(ORG_ADMIN, other)

    assert unseen.value.code is ErrorCode.STAFF_NOT_FOUND


def test_the_principals_used_here_are_what_the_tests_think_they_are() -> None:
    assert isinstance(ORG_ADMIN, Principal)
