"""RoutingService (ADR 0081): who changes a group's subjects and routing, and what is audited."""

from datetime import time

import pytest

from app.domain import audit
from app.domain.admin import AdminService
from app.domain.errors import DomainError, ErrorCode
from app.domain.routing import SUBJECT_SOURCE, Attribute, RouteVariable, Subject
from app.domain.routing_service import RoutingService
from app.domain.scheduling import ScheduleRule, TimeWindow
from tests.fakes import clock as fake_clock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, two_organisations
from tests.support.domain import NOW


def subject(code: str, nl: str = "Onderwerp", en: str = "Subject") -> Subject:
    return Subject.create(code, {"nl": nl, "en": en})


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


def _service(uow: InMemoryUnitOfWork, limit: int = 3) -> RoutingService:
    clock = fake_clock.FixedClock(NOW)
    return RoutingService(uow, clock, AdminService(uow, clock), lambda: limit)


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> RoutingService:
    return _service(uow)


def details(uow: InMemoryUnitOfWork) -> list[str]:
    return [e.detail for e in uow.state.audit if e.outcome is audit.Outcome.SUCCESS]


async def test_a_group_starts_without_routing(service: RoutingService) -> None:
    view = await service.view(ORG_ADMIN, "demo")
    assert (view.routing.subjects, view.can_add, view.max_subjects) == ((), True, 3)


async def test_subjects_are_added_edited_moved_and_removed_and_audited_by_code(
    service: RoutingService, uow: InMemoryUnitOfWork
) -> None:
    await service.add_subject(ORG_ADMIN, "demo", subject("general"))
    await service.add_subject(ORG_ADMIN, "demo", subject("move", "Verhuizing", "Moving"))
    await service.update_subject(ORG_ADMIN, "demo", "general", {"nl": "Vraag", "en": "Question"})
    await service.move_subject(ORG_ADMIN, "demo", "move", -1)
    await service.move_subject(ORG_ADMIN, "demo", "move", -1)  # already first: no change
    routing = uow.state.groups[1].routing
    assert [s.code for s in routing.subjects] == ["move", "general"]
    assert routing.subject("general").label("en") == "Question"  # type: ignore[union-attr]
    await service.remove_subject(ORG_ADMIN, "demo", "general")
    assert [s.code for s in uow.state.groups[1].routing.subjects] == ["move"]
    assert details(uow) == [
        "subject.add,code:general",
        "subject.add,code:move",
        "subject.update,code:general",
        "subject.move,code:move",
        "subject.remove,code:general",
    ]
    assert not any("Verhuizing" in d or "Question" in d for d in details(uow))


async def test_a_taken_code_and_the_limit_are_refused(uow: InMemoryUnitOfWork) -> None:
    service = _service(uow, limit=1)
    await service.add_subject(ORG_ADMIN, "demo", subject("general"))
    with pytest.raises(DomainError) as taken:
        await service.add_subject(ORG_ADMIN, "demo", subject("general"))
    assert taken.value.field == "subject_code"
    with pytest.raises(DomainError) as full:
        await service.add_subject(ORG_ADMIN, "demo", subject("move"))
    assert full.value.field == "subjects"
    assert not (await service.view(ORG_ADMIN, "demo")).can_add


async def test_a_subject_a_block_offers_cannot_be_removed(
    service: RoutingService, uow: InMemoryUnitOfWork
) -> None:
    await service.add_subject(ORG_ADMIN, "demo", subject("move"))
    window = TimeWindow(time(9), time(12), 2, subjects=("move",))
    async with uow.locked() as tx:
        await tx.add_schedule_rule(1, ScheduleRule(0, window))
    assert (await service.view(ORG_ADMIN, "demo")).bound == frozenset({"move"})
    with pytest.raises(DomainError) as refused:
        await service.remove_subject(ORG_ADMIN, "demo", "move")
    assert refused.value.field == "subject_in_use"


async def test_attributes_and_mapping_are_set_and_audited_by_source(
    service: RoutingService, uow: InMemoryUnitOfWork
) -> None:
    await service.add_subject(ORG_ADMIN, "demo", subject("move"))
    await service.set_attributes(ORG_ADMIN, "demo", {Attribute.PREFERRED_LANGUAGE})
    await service.set_attributes(ORG_ADMIN, "demo", {Attribute.PREFERRED_LANGUAGE})  # unchanged
    await service.set_mapping(
        ORG_ADMIN, "demo", SUBJECT_SOURCE, "topic", {"move": "MV", "gone": "X", "general": ""}
    )
    await service.set_mapping(ORG_ADMIN, "demo", "preferred_language", "lang", {})
    routing = uow.state.groups[1].routing
    assert routing.attributes == (Attribute.PREFERRED_LANGUAGE,)
    assert routing.variable(SUBJECT_SOURCE) == RouteVariable("topic", (("move", "MV"),))
    await service.set_mapping(ORG_ADMIN, "demo", "preferred_language", "", {})
    assert uow.state.groups[1].routing.variable("preferred_language") is None
    assert details(uow)[1:] == [
        "attributes.set,on:preferred_language",
        "mapping.set,source:subject",
        "mapping.set,source:preferred_language",
        "mapping.set,source:preferred_language",
    ]
    with pytest.raises(DomainError):
        await service.set_mapping(ORG_ADMIN, "demo", "channel_preference", "ch", {})


@pytest.mark.parametrize("principal", [GROUP_ADMIN, OUTSIDER])
async def test_only_who_edits_the_group_may_change_its_routing(
    service: RoutingService, uow: InMemoryUnitOfWork, principal: object
) -> None:
    with pytest.raises(DomainError) as caught:
        await service.add_subject(principal, "demo", subject("move"))  # type: ignore[arg-type]
    assert caught.value.code is ErrorCode.NOT_PERMITTED
    assert uow.state.groups[1].routing.subjects == ()
    assert uow.state.audit[-1].outcome is audit.Outcome.DENIED


async def test_an_archived_group_is_read_only(
    service: RoutingService, uow: InMemoryUnitOfWork
) -> None:
    from dataclasses import replace

    uow.state.groups[1] = replace(uow.state.groups[1], archived_at=NOW)
    with pytest.raises(DomainError) as caught:
        await service.add_subject(SUPER, "demo", subject("move"))
    assert caught.value.code is ErrorCode.GROUP_ARCHIVED
