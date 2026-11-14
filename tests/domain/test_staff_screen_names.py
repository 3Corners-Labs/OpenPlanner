"""Setting a screen name and finding who a booking's claim names (ADR 0123 step 5c)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.audit import Outcome
from app.domain.authorization import Role
from app.domain.errors import DomainError, ErrorCode
from app.domain.guest_access import StaffNameMode
from app.domain.screen_name import NO_LABEL, ScreenNameError, ScreenNameRefusal, StaffLabel
from app.domain.staff import SCREEN_NAME_EVENT, Membership, StaffMember, StaffStatus
from app.domain.staff_screen_names import (
    CLEARED,
    REBUILD_FLOOR,
    SET,
    ScreenNameResult,
    StaffScreenNames,
)
from app.domain.tokens import agent_pseudonym
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import AUDITOR, GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, two_organisations
from tests.support.domain import NOW

KEY = b"k" * 32
PREVIOUS = b"p" * 32
DEMO = Membership("group_admin", "demo", "demo")


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def service(uow: InMemoryUnitOfWork, clock: FixedClock) -> StaffScreenNames:
    return StaffScreenNames(uow, clock, [KEY, PREVIOUS])


def _person(uow: InMemoryUnitOfWork, subject: str = "p-1", **changes: object) -> StaffMember:
    member = StaffMember(
        subject,
        "Paula Person",
        frozenset({Role.GROUP_ADMIN}),
        frozenset({DEMO}),
        NOW,
        NOW,
        NOW,
        id=len(uow.state.staff) + 10,
    )
    member = replace(member, **changes)  # type: ignore[arg-type]
    uow.state.staff[subject] = member
    return member


def _audit(uow: InMemoryUnitOfWork) -> list[tuple[str, str, Outcome, str, str | None, str]]:
    return [
        (e.actor, e.action, e.outcome, e.reason, e.organisation, e.detail) for e in uow.state.audit
    ]


# ---- setting the name ----------------------------------------------------------------------------


async def test_a_manager_sets_the_name_and_the_event_says_that_it_changed_and_not_to_what(
    uow: InMemoryUnitOfWork, service: StaffScreenNames
) -> None:
    person = _person(uow)

    result = await service.set_screen_name(ORG_ADMIN, person.id or 0, "  Sanne ")

    assert result is ScreenNameResult.DONE
    assert uow.state.staff["p-1"].screen_name == "Sanne"
    assert _audit(uow) == [("o-1", SCREEN_NAME_EVENT, Outcome.SUCCESS, SET, "demo", "subject:p-1")]
    assert all("Sanne" not in repr(row) for row in _audit(uow))


async def test_an_empty_text_clears_the_name_and_says_so_in_the_event(
    uow: InMemoryUnitOfWork, service: StaffScreenNames
) -> None:
    person = _person(uow, screen_name="Sanne")

    result = await service.set_screen_name(ORG_ADMIN, person.id or 0, "")

    assert result is ScreenNameResult.CLEARED
    assert uow.state.staff["p-1"].screen_name is None
    assert [row[3] for row in _audit(uow)] == [CLEARED]


async def test_the_same_name_again_changes_nothing_and_writes_no_event(
    uow: InMemoryUnitOfWork, service: StaffScreenNames
) -> None:
    person = _person(uow, screen_name="Sanne")

    again = await service.set_screen_name(ORG_ADMIN, person.id or 0, "Sanne")
    nothing = await service.set_screen_name(ORG_ADMIN, _person(uow, "p-2").id or 0, "")

    assert (again, nothing) == (ScreenNameResult.UNCHANGED, ScreenNameResult.UNCHANGED)
    assert uow.state.audit == []


async def test_a_text_that_is_not_a_screen_name_is_refused_with_a_code_before_anything_is_read(
    uow: InMemoryUnitOfWork, service: StaffScreenNames
) -> None:
    person = _person(uow)

    with pytest.raises(ScreenNameError) as refused:
        await service.set_screen_name(ORG_ADMIN, person.id or 0, "<script>")

    assert refused.value.reason is ScreenNameRefusal.BAD_CHARACTER
    assert uow.state.staff["p-1"].screen_name is None
    assert uow.state.audit == []


@pytest.mark.parametrize("who", [AUDITOR, GROUP_ADMIN])
async def test_whoever_holds_no_staff_manage_right_is_refused_by_the_service_itself(
    uow: InMemoryUnitOfWork, service: StaffScreenNames, who: object
) -> None:
    person = _person(uow)

    with pytest.raises(DomainError) as refused:
        await service.set_screen_name(who, person.id or 0, "Sanne")  # type: ignore[arg-type]

    assert refused.value.code is ErrorCode.NOT_PERMITTED
    assert uow.state.staff["p-1"].screen_name is None


async def test_a_person_outside_the_managers_scope_does_not_exist_and_the_attempt_is_audited(
    uow: InMemoryUnitOfWork, service: StaffScreenNames
) -> None:
    person = _person(uow)

    result = await service.set_screen_name(OUTSIDER, person.id or 0, "Sanne")
    unknown = await service.set_screen_name(OUTSIDER, 999, "Sanne")

    assert (result, unknown) == (ScreenNameResult.OUT_OF_SCOPE, ScreenNameResult.OUT_OF_SCOPE)
    assert uow.state.staff["p-1"].screen_name is None
    assert [row[2:4] for row in _audit(uow)] == [(Outcome.DENIED, "out_of_scope")] * 2


async def test_only_the_superuser_names_a_superuser(
    uow: InMemoryUnitOfWork, service: StaffScreenNames
) -> None:
    boss = _person(uow, "boss", roles=frozenset({Role.SUPERUSER}))

    assert (
        await service.set_screen_name(ORG_ADMIN, boss.id or 0, "Boss")
    ) is ScreenNameResult.OUT_OF_SCOPE
    assert (await service.set_screen_name(SUPER, boss.id or 0, "Boss")) is ScreenNameResult.DONE


async def test_an_erased_record_keeps_no_name_and_gets_none(
    uow: InMemoryUnitOfWork, service: StaffScreenNames
) -> None:
    person = _person(uow, screen_name="Sanne")
    uow.state.staff["p-1"] = person.erased(NOW)

    result = await service.set_screen_name(SUPER, person.id or 0, "Sanne")

    assert result is ScreenNameResult.ERASED
    assert uow.state.staff["p-1"].screen_name is None


async def test_erasing_the_record_clears_the_screen_name() -> None:
    member = StaffMember(
        "p-1", "P", frozenset(), frozenset(), NOW, NOW, NOW, id=1, screen_name="Sanne"
    )

    assert member.erased(NOW).screen_name is None
    assert "Sanne" not in repr(member)


# ---- naming who accepted ----------------------------------------------------------------------


async def test_the_claims_pseudonym_finds_the_screen_name_for_every_setting(
    uow: InMemoryUnitOfWork, service: StaffScreenNames
) -> None:
    _person(uow, screen_name="Sanne")
    pseudonym = agent_pseudonym(KEY, "p-1")

    by_mode = {mode: await service.label_of(pseudonym, mode) for mode in StaffNameMode}

    assert by_mode == {
        StaffNameMode.SCREEN_NAME: StaffLabel("Sanne"),
        StaffNameMode.NAME: StaffLabel("Paula Person"),
        StaffNameMode.TERM: NO_LABEL,
    }


async def test_without_a_screen_name_the_account_name_is_never_the_fallback(
    uow: InMemoryUnitOfWork, service: StaffScreenNames
) -> None:
    _person(uow)

    label = await service.label_of(agent_pseudonym(KEY, "p-1"), StaffNameMode.SCREEN_NAME)

    assert label == NO_LABEL


async def test_a_pseudonym_under_the_previous_key_still_finds_the_person(
    uow: InMemoryUnitOfWork, service: StaffScreenNames
) -> None:
    _person(uow, screen_name="Sanne")

    label = await service.label_of(agent_pseudonym(PREVIOUS, "p-1"), StaffNameMode.SCREEN_NAME)

    assert label.name == "Sanne"


async def test_nobody_accepted_or_the_claim_is_unknown_means_no_name(
    uow: InMemoryUnitOfWork, service: StaffScreenNames
) -> None:
    _person(uow, screen_name="Sanne")

    assert await service.label_of(None, StaffNameMode.SCREEN_NAME) == NO_LABEL
    assert await service.label_of("0" * 64, StaffNameMode.SCREEN_NAME) == NO_LABEL


async def test_an_erased_person_has_no_name_even_when_the_map_still_holds_the_pseudonym(
    uow: InMemoryUnitOfWork, service: StaffScreenNames
) -> None:
    person = _person(uow, screen_name="Sanne")
    pseudonym = agent_pseudonym(KEY, "p-1")
    assert (await service.label_of(pseudonym, StaffNameMode.SCREEN_NAME)).name == "Sanne"

    uow.state.staff["p-1"] = person.erased(NOW)

    assert await service.label_of(pseudonym, StaffNameMode.SCREEN_NAME) == NO_LABEL
    assert await service.label_of(pseudonym, StaffNameMode.NAME) == NO_LABEL


async def test_a_changed_or_cleared_screen_name_shows_at_once_the_map_never_holds_a_name(
    uow: InMemoryUnitOfWork, service: StaffScreenNames
) -> None:
    person = _person(uow, screen_name="Sanne")
    pseudonym = agent_pseudonym(KEY, "p-1")
    assert (await service.label_of(pseudonym, StaffNameMode.SCREEN_NAME)).name == "Sanne"

    await service.set_screen_name(ORG_ADMIN, person.id or 0, "Sam")
    assert (await service.label_of(pseudonym, StaffNameMode.SCREEN_NAME)).name == "Sam"
    await service.set_screen_name(ORG_ADMIN, person.id or 0, "")
    assert await service.label_of(pseudonym, StaffNameMode.SCREEN_NAME) == NO_LABEL


class _Counting(InMemoryUnitOfWork):
    """A unit of work that counts the scans of the whole directory."""

    scans = 0

    def read(self):  # type: ignore[no-untyped-def, override]
        outer = super().read()
        counter = self

        class _Tx:
            async def __aenter__(self) -> object:
                tx = await outer.__aenter__()
                original = tx.list_staff

                async def counted(*args: object, **kwargs: object) -> object:
                    counter.scans += 1
                    return await original(*args, **kwargs)

                tx.list_staff = counted
                return tx

            async def __aexit__(self, *exc: object) -> object:
                return await outer.__aexit__(*exc)

        return _Tx()


async def test_the_directory_is_not_scanned_on_every_ask_and_a_miss_waits_for_the_floor(
    clock: FixedClock,
) -> None:
    uow = _Counting(two_organisations())
    service = StaffScreenNames(uow, clock, [KEY])
    _person(uow, screen_name="Sanne")
    known = agent_pseudonym(KEY, "p-1")

    for _ in range(5):
        assert (await service.label_of(known, StaffNameMode.SCREEN_NAME)).name == "Sanne"
    assert uow.scans == 1

    # A claim that matches nobody scans once, then no more until the floor has passed.
    for _ in range(5):
        assert await service.label_of("f" * 64, StaffNameMode.SCREEN_NAME) == NO_LABEL
    assert uow.scans == 1
    # A new staff member shows up after the floor, not before.
    _person(uow, "p-2", screen_name="Tom")
    newcomer = agent_pseudonym(KEY, "p-2")
    assert await service.label_of(newcomer, StaffNameMode.SCREEN_NAME) == NO_LABEL
    clock.advance(REBUILD_FLOOR + timedelta(seconds=1))
    assert (await service.label_of(newcomer, StaffNameMode.SCREEN_NAME)).name == "Tom"
    assert uow.scans == 2


async def test_a_suspended_person_is_still_named_the_call_is_not_theirs_to_lose(
    uow: InMemoryUnitOfWork, service: StaffScreenNames
) -> None:
    _person(uow, screen_name="Sanne", status=StaffStatus.SUSPENDED)

    label = await service.label_of(agent_pseudonym(KEY, "p-1"), StaffNameMode.SCREEN_NAME)

    assert label.name == "Sanne"


# ---- a review of step 5c: several organisations, the one map ------------------------------


async def test_the_audit_event_is_written_for_each_organisation_of_the_person_in_the_scope(
    uow: InMemoryUnitOfWork, service: StaffScreenNames
) -> None:
    both = frozenset({DEMO, Membership("org_admin", "andere")})
    person = _person(uow, memberships=both)

    await service.set_screen_name(ORG_ADMIN, person.id or 0, "Sanne")  # manages demo only
    assert [row[4] for row in _audit(uow)] == ["demo"]

    uow.state.audit.clear()
    await service.set_screen_name(SUPER, person.id or 0, "Sam")  # manages both
    assert sorted(row[4] or "" for row in _audit(uow)) == ["andere", "demo"]
    # One person, one name: it is the name every organisation's participants see.
    assert uow.state.staff["p-1"].screen_name == "Sam"


async def test_the_pseudonym_map_is_the_one_function_both_homes_use() -> None:
    from app.domain.tokens import staff_ids_by_pseudonym

    found = staff_ids_by_pseudonym([KEY, PREVIOUS], [(1, "a"), (None, "b"), (3, "c")])

    assert found == {
        agent_pseudonym(KEY, "a"): 1,
        agent_pseudonym(PREVIOUS, "a"): 1,
        agent_pseudonym(KEY, "c"): 3,
        agent_pseudonym(PREVIOUS, "c"): 3,
    }


# ---- the person's own name (step 5d) -------------------------------------------------------------


async def test_the_person_sets_their_own_name_found_by_their_subject_in_every_organisation(
    uow: InMemoryUnitOfWork, service: StaffScreenNames
) -> None:
    both = (Membership("org_admin", "demo", None), Membership("org_admin", "andere", None))
    _person(uow, "p-1", memberships=frozenset(both))
    _person(uow, "p-2")

    result = await service.set_own_screen_name(replace(AUDITOR, subject="p-1"), "  Sanne ")

    assert result is ScreenNameResult.DONE
    assert uow.state.staff["p-1"].screen_name == "Sanne"
    assert uow.state.staff["p-2"].screen_name is None
    rows = _audit(uow)
    assert sorted(str(row[4]) for row in rows) == ["andere", "demo"]
    assert {(row[0], row[3], row[5]) for row in rows} == {("p-1", SET, "subject:p-1")}
    assert all("Sanne" not in repr(row) for row in rows)


async def test_the_own_write_clears_keeps_and_refuses_like_the_managers(
    uow: InMemoryUnitOfWork, service: StaffScreenNames
) -> None:
    _person(uow, "p-1", screen_name="Sanne")
    me = replace(AUDITOR, subject="p-1")

    assert await service.set_own_screen_name(me, "Sanne") is ScreenNameResult.UNCHANGED
    assert await service.set_own_screen_name(me, "") is ScreenNameResult.CLEARED
    assert [row[3] for row in _audit(uow)] == [CLEARED]
    with pytest.raises(ScreenNameError):
        await service.set_own_screen_name(me, "G")


async def test_the_own_write_needs_a_role_a_record_and_a_record_that_is_not_erased(
    uow: InMemoryUnitOfWork, service: StaffScreenNames
) -> None:
    me = replace(AUDITOR, subject="p-1")
    with pytest.raises(DomainError) as error:
        await service.set_own_screen_name(replace(AUDITOR, grants=frozenset()), "Sanne")
    assert error.value.code is ErrorCode.NOT_PERMITTED

    assert await service.set_own_screen_name(me, "Sanne") is ScreenNameResult.NO_RECORD
    assert await service.own("p-1") is None
    _person(uow, "p-1", status=StaffStatus.ERASED)
    assert await service.set_own_screen_name(me, "Sanne") is ScreenNameResult.ERASED
    assert uow.state.audit == []
