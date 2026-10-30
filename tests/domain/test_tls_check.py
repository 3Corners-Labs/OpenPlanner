"""The worker's certificate watch: schedule, storage and system events (ADR 0064)."""

from datetime import timedelta

import pytest

from app.domain.audit import Outcome
from app.domain.system_log import SystemLog
from app.domain.tls import EVENT, Address, Certificate, Finding, ProbeResult, Trust
from app.domain.tls_check import CertificateStatus, TlsWatch
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.fakes.tls import FakeCertificateProbe
from tests.support.domain import NOW

INTERVAL = timedelta(hours=6)
WARN = timedelta(days=20)
REPEAT = timedelta(hours=24)
HOST = "afspraak.example.nl"


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def probe() -> FakeCertificateProbe:
    return FakeCertificateProbe(trusted(60))


def trusted(days_left: int) -> ProbeResult:
    return ProbeResult(
        Certificate(
            HOST,
            "Let's Encrypt (R11)",
            NOW - timedelta(days=30),
            NOW + timedelta(days=days_left),
            (HOST,),
        ),
        Trust.TRUSTED,
    )


def watch(
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
    probe: FakeCertificateProbe,
    url: str = f"https://{HOST}",
) -> TlsWatch:
    return TlsWatch(
        uow,
        clock,
        probe,
        SystemLog(uow, clock),
        public_base_url=url,
        interval=INTERVAL,
        warn_within=WARN,
        repeat=REPEAT,
    )


def events(uow: InMemoryUnitOfWork) -> list[tuple[Outcome, str, str]]:
    return [(e.outcome, e.reason, e.detail) for e in uow.state.audit if e.action == EVENT]


async def test_the_first_round_probes_and_stores_without_an_event_when_ok(
    uow: InMemoryUnitOfWork, clock: FixedClock, probe: FakeCertificateProbe
) -> None:
    check = await watch(uow, clock, probe).check_if_due()

    assert check is not None
    assert check.finding is Finding.OK
    assert probe.asked == [(HOST, 443)]
    assert await CertificateStatus(uow).latest() == check
    assert events(uow) == []


async def test_the_probe_waits_for_the_interval(
    uow: InMemoryUnitOfWork, clock: FixedClock, probe: FakeCertificateProbe
) -> None:
    watcher = watch(uow, clock, probe)
    await watcher.check_if_due()

    clock.advance(INTERVAL - timedelta(minutes=1))
    assert await watcher.check_if_due() is None
    clock.advance(timedelta(minutes=1))
    assert await watcher.check_if_due() is not None
    assert len(probe.asked) == 2


async def test_a_problem_is_reported_when_it_appears_and_repeated_while_it_lasts(
    uow: InMemoryUnitOfWork, clock: FixedClock, probe: FakeCertificateProbe
) -> None:
    watcher = watch(uow, clock, probe)
    await watcher.check_if_due()
    probe.result = trusted(10)

    clock.advance(INTERVAL)
    await watcher.check_if_due()
    clock.advance(INTERVAL)
    await watcher.check_if_due()  # same finding, within the repeat period: quiet
    clock.advance(REPEAT)
    await watcher.check_if_due()  # repeated

    assert events(uow) == [
        (Outcome.FAILURE, "expiring", "days:9,issuer:Let's Encrypt (R11)"),
        (Outcome.FAILURE, "expiring", "days:8,issuer:Let's Encrypt (R11)"),
    ]


async def test_a_changed_problem_is_reported_at_once(
    uow: InMemoryUnitOfWork, clock: FixedClock, probe: FakeCertificateProbe
) -> None:
    watcher = watch(uow, clock, probe)
    probe.result = trusted(10)
    await watcher.check_if_due()
    probe.result = ProbeResult(error="unreachable")

    clock.advance(INTERVAL)
    await watcher.check_if_due()

    assert events(uow) == [
        (Outcome.FAILURE, "expiring", "days:10,issuer:Let's Encrypt (R11)"),
        (Outcome.FAILURE, "unreachable", "error:unreachable"),
    ]


async def test_the_return_to_ok_is_recorded_once(
    uow: InMemoryUnitOfWork, clock: FixedClock, probe: FakeCertificateProbe
) -> None:
    watcher = watch(uow, clock, probe)
    probe.result = trusted(10)
    await watcher.check_if_due()
    probe.result = trusted(89)

    clock.advance(INTERVAL)
    await watcher.check_if_due()
    clock.advance(INTERVAL)
    await watcher.check_if_due()

    assert events(uow)[1:] == [(Outcome.SUCCESS, "ok", "days:88,issuer:Let's Encrypt (R11)")]


async def test_an_http_address_has_nothing_to_check(
    uow: InMemoryUnitOfWork, clock: FixedClock, probe: FakeCertificateProbe
) -> None:
    check = await watch(uow, clock, probe, "http://127.0.0.1:8000").check_if_due()

    assert check is not None
    assert check.finding is Finding.NOT_HTTPS
    assert check.host == "127.0.0.1"
    assert probe.asked == []
    # Not a failing certificate: no event, but the stored check tells the status page.
    assert [(e.outcome, e.reason) for e in uow.state.audit if e.action == EVENT] == [
        (Outcome.FAILURE, "not_https")
    ]


async def test_probe_takes_another_address_without_storing(
    uow: InMemoryUnitOfWork, clock: FixedClock, probe: FakeCertificateProbe
) -> None:
    check = await watch(uow, clock, probe).probe(Address("booking.test", 8443))

    assert check.host == "booking.test"
    assert probe.asked == [("booking.test", 8443)]
    assert await CertificateStatus(uow).latest() is None
