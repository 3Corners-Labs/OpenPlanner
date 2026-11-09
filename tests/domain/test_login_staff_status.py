"""A suspended person cannot log in, an erased one starts afresh (ADR 0046 step 2)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.authorization import Role
from app.domain.entitlements import ClaimMode, LoginClaims
from app.domain.login_service import SUSPENDED, ClaimSettings, LoginRefusedError, LoginService
from app.domain.staff import CREATED_EVENT, StaffStatus
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import two_organisations
from tests.support.domain import NOW

CLAIMS = LoginClaims(
    "sub-1", "Gina Group", roles=frozenset({Role.GROUP_ADMIN}), groups=frozenset({"demo"})
)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


def _service(uow: InMemoryUnitOfWork, clock: FixedClock) -> LoginService:
    return LoginService(
        uow, AdminService(uow, clock), lambda: ClaimSettings(ClaimMode.LEGACY, "openplanner")
    )


async def _set(uow: InMemoryUnitOfWork, status: StaffStatus) -> None:
    async with uow.locked() as tx:
        await tx.set_staff_status("sub-1", status, NOW)


async def test_a_suspended_person_is_refused_whatever_the_claims_say(
    uow: InMemoryUnitOfWork,
) -> None:
    clock = FixedClock(NOW)
    service = _service(uow, clock)
    await service.complete_login(CLAIMS)
    await _set(uow, StaffStatus.SUSPENDED)
    before = uow.state.staff["sub-1"]

    with pytest.raises(LoginRefusedError) as refused:
        await service.complete_login(CLAIMS)

    assert refused.value.reason == SUSPENDED
    assert uow.state.staff["sub-1"] == before  # a refused login records nothing


async def test_after_reinstating_the_person_logs_in_again(uow: InMemoryUnitOfWork) -> None:
    clock = FixedClock(NOW)
    service = _service(uow, clock)
    await service.complete_login(CLAIMS)
    await _set(uow, StaffStatus.SUSPENDED)
    await _set(uow, StaffStatus.ACTIVE)

    outcome = await service.complete_login(CLAIMS)

    assert outcome.principal.subject == "sub-1"


async def test_an_erased_person_who_returns_starts_a_fresh_record(
    uow: InMemoryUnitOfWork,
) -> None:
    clock = FixedClock(NOW)
    service = _service(uow, clock)
    await service.complete_login(CLAIMS)
    async with uow.locked() as tx:
        await tx.erase_staff("sub-1", NOW)
    clock.advance(timedelta(days=400))

    await service.complete_login(CLAIMS)

    member = uow.state.staff["sub-1"]
    assert (member.status, member.display_name, member.first_seen_at) == (
        StaffStatus.ACTIVE,
        "Gina Group",
        clock.now(),
    )
    assert [e.action for e in uow.state.audit if e.action == CREATED_EVENT] == [
        CREATED_EVENT,
        CREATED_EVENT,
    ]


async def test_the_login_address_goes_to_the_staff_record_and_not_into_the_session_claims(
    uow: InMemoryUnitOfWork,
) -> None:
    # ADR 0117 step 10b
    service = _service(uow, FixedClock(NOW))
    with_address = replace(CLAIMS, email="gina@voorbeeld.nl")

    outcome = await service.complete_login(with_address)

    assert uow.state.staff["sub-1"].email == "gina@voorbeeld.nl"
    assert outcome.session_claims.email is None  # the session row never holds the address

    await service.complete_login(CLAIMS)  # no verified address this time
    assert uow.state.staff["sub-1"].email is None
