"""The worker's round trip to the public address: schedule, storage and events (ADR 0086)."""

from datetime import timedelta

import pytest

from app.domain.address_check import AddressStatus, AddressWatch, InstanceService
from app.domain.audit import Outcome
from app.domain.public_address import (
    EVENT,
    AddressFinding,
    AddressKind,
    Forwarded,
    InstanceAnswer,
    InstanceReply,
    nonce_hash,
)
from app.domain.system_log import SystemLog
from tests.fakes.clock import FixedClock
from tests.fakes.instance import FakeInstanceProbe, healthy
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import NOW

INTERVAL = timedelta(hours=6)
REPEAT = timedelta(hours=24)
PUBLIC = "https://afspraak.example.nl"
HOOK = "https://hooks.example.nl"
ID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork()


@pytest.fixture
def probe() -> FakeInstanceProbe:
    return FakeInstanceProbe(healthy(ID))


def watch(
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
    probe: FakeInstanceProbe,
    *,
    webhook: str | None = None,
    enabled: bool = True,
) -> AddressWatch:
    return AddressWatch(
        uow,
        clock,
        probe,
        SystemLog(uow, clock),
        public_base_url=PUBLIC,
        webhook_base_url=webhook,
        interval=INTERVAL,
        repeat=REPEAT,
        enabled=enabled,
        make_nonce=lambda: "nonce-1",
    )


def events(uow: InMemoryUnitOfWork) -> list[tuple[Outcome, str, str]]:
    return [(e.outcome, e.reason, e.detail) for e in uow.state.audit if e.action == EVENT]


async def test_the_first_round_asks_with_a_stored_nonce_and_stores_the_finding(
    uow: InMemoryUnitOfWork, clock: FixedClock, probe: FakeInstanceProbe
) -> None:
    checks = await watch(uow, clock, probe).check_if_due()

    assert [(c.kind, c.finding, c.url) for c in checks] == [
        (AddressKind.PUBLIC, AddressFinding.OK, PUBLIC)
    ]
    assert probe.asked == [(PUBLIC, "nonce-1")]
    # The database holds only the hash, stored before the request was made.
    assert uow.state.probe_nonce == (nonce_hash("nonce-1"), NOW)
    assert (await AddressStatus(uow).latest())[AddressKind.PUBLIC].scheme == "https"
    assert events(uow) == []


async def test_the_round_waits_for_the_interval_and_runs_again_after_it(
    uow: InMemoryUnitOfWork, clock: FixedClock, probe: FakeInstanceProbe
) -> None:
    watcher = watch(uow, clock, probe)
    await watcher.check_if_due()

    clock.advance(INTERVAL - timedelta(minutes=1))
    assert await watcher.check_if_due() == []
    clock.advance(timedelta(minutes=2))
    assert len(await watcher.check_if_due()) == 1
    assert len(probe.asked) == 2


async def test_a_switched_off_probe_asks_nothing(
    uow: InMemoryUnitOfWork, clock: FixedClock, probe: FakeInstanceProbe
) -> None:
    assert await watch(uow, clock, probe, enabled=False).check_if_due() == []
    assert probe.asked == []


async def test_a_problem_is_reported_when_it_appears_repeated_while_it_lasts_and_cleared_once(
    uow: InMemoryUnitOfWork, clock: FixedClock, probe: FakeInstanceProbe
) -> None:
    watcher = watch(uow, clock, probe)
    probe.reply = InstanceReply(InstanceAnswer(ID, "http", Forwarded.IGNORED))

    await watcher.check()
    clock.advance(INTERVAL)
    await watcher.check_if_due()
    assert events(uow) == [(Outcome.FAILURE, "proxy_not_trusted", "address:public")]
    clock.advance(REPEAT)
    await watcher.check_if_due()
    probe.reply = healthy(ID)
    clock.advance(INTERVAL)
    await watcher.check_if_due()
    clock.advance(INTERVAL)
    await watcher.check_if_due()

    assert [e[:2] for e in events(uow)] == [
        (Outcome.FAILURE, "proxy_not_trusted"),
        (Outcome.FAILURE, "proxy_not_trusted"),
        (Outcome.SUCCESS, "ok"),
    ]


async def test_a_changed_problem_is_reported_at_once(
    uow: InMemoryUnitOfWork, clock: FixedClock, probe: FakeInstanceProbe
) -> None:
    watcher = watch(uow, clock, probe)
    probe.reply = InstanceReply(error="timeout")
    await watcher.check()
    probe.reply = InstanceReply(InstanceAnswer("another-installation"))
    await watcher.check()

    assert events(uow) == [
        (Outcome.FAILURE, "unreachable", "address:public,error:timeout"),
        (Outcome.FAILURE, "other_instance", "address:public"),
    ]


async def test_an_event_names_codes_only(
    uow: InMemoryUnitOfWork, clock: FixedClock, probe: FakeInstanceProbe
) -> None:
    probe.reply = InstanceReply(error="http_502")

    await watch(uow, clock, probe).check()

    event = next(e for e in uow.state.audit if e.action == EVENT)
    assert PUBLIC not in f"{event.reason}{event.detail}{event.message}"
    assert event.organisation is None
    assert event.group is None


async def test_the_webhook_address_is_probed_when_it_differs_and_forgotten_when_unset(
    uow: InMemoryUnitOfWork, clock: FixedClock, probe: FakeInstanceProbe
) -> None:
    probe.by_url[HOOK] = InstanceReply(redirected=True)

    checks = await watch(uow, clock, probe, webhook=HOOK).check()

    assert {c.kind: c.finding for c in checks} == {
        AddressKind.PUBLIC: AddressFinding.OK,
        AddressKind.WEBHOOK: AddressFinding.REDIRECTED,
    }
    assert events(uow) == [(Outcome.FAILURE, "redirected", "address:webhook")]
    await watch(uow, clock, probe, webhook=PUBLIC + "/").check()
    assert set(await AddressStatus(uow).latest()) == {AddressKind.PUBLIC}


async def test_a_new_webhook_address_makes_the_round_due_before_the_interval(
    uow: InMemoryUnitOfWork, clock: FixedClock, probe: FakeInstanceProbe
) -> None:
    await watch(uow, clock, probe).check_if_due()

    assert len(await watch(uow, clock, probe, webhook=HOOK).check_if_due()) == 2


async def test_the_instance_id_is_made_once_and_the_detail_goes_to_the_holder_of_the_nonce_only(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    service = InstanceService(uow, clock)
    await service.prepare()
    await uow_store_nonce(uow, clock, "secret-nonce")

    stranger = await service.answer(None, scheme="https", forwarded=Forwarded.HONOURED)
    guess = await service.answer("guess", scheme="https", forwarded=Forwarded.HONOURED)
    holder = await service.answer("secret-nonce", scheme="https", forwarded=Forwarded.HONOURED)
    again = await service.answer("secret-nonce", scheme="https", forwarded=Forwarded.HONOURED)

    assert stranger == InstanceAnswer(ID)
    assert guess == InstanceAnswer(ID)
    assert holder == InstanceAnswer(ID, "https", Forwarded.HONOURED)
    # Consumed on first use: a replay gets the id only.
    assert again == InstanceAnswer(ID)


async def test_a_nonce_older_than_its_lifetime_is_not_accepted(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    service = InstanceService(uow, clock)
    await uow_store_nonce(uow, clock, "old")
    clock.advance(timedelta(minutes=6))

    answer = await service.answer("old", scheme="https", forwarded=Forwarded.HONOURED)

    assert answer.scheme is None
    assert answer.forwarded is None


async def test_an_installation_without_an_id_makes_one_on_the_first_question(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    answer = await InstanceService(uow, clock).answer(
        None, scheme="http", forwarded=Forwarded.ABSENT
    )

    assert answer.instance_id == ID
    assert uow.state.instance_id == ID


async def uow_store_nonce(uow: InMemoryUnitOfWork, clock: FixedClock, nonce: str) -> None:
    async with uow.locked() as tx:
        await tx.store_probe_nonce(nonce_hash(nonce), clock.now())
