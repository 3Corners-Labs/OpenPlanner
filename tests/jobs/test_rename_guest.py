"""The guest's rename at the purge (ADR 0123 point 7): when it is enqueued and how it runs."""

import pytest

from app.domain.audit import Outcome
from app.domain.guest_access import NEUTRAL_TERM, guest_subject
from app.domain.job_plan import BACKOFF, JobType
from app.domain.ports import IntegrationError
from app.jobs.calls import NO_GUEST_KEY
from tests.fakes.jobs import FakeQueue, job
from tests.support.call_jobs import WIRED, Setup, group_uow
from tests.support.domain import NOW

CURRENT = b"c" * 32
PREVIOUS = b"p" * 32


@pytest.fixture
def setup() -> Setup:
    return Setup(group_uow(**WIRED))


def _renames(setup: Setup, booking_id: int) -> list[JobType]:
    """The rename jobs waiting for the booking."""
    return [
        j.type for j in setup.uow.state.jobs.get(booking_id, []) if j.type is JobType.RENAME_GUEST
    ]


async def _purge(setup: Setup, booking_id: int) -> None:
    async with setup.uow.locked() as tx:
        await tx.purge_personal_data(booking_id, NOW)


async def test_a_purge_enqueues_one_rename_when_a_guest_was_minted(setup: Setup) -> None:
    bid = await setup.booking()
    async with setup.uow.locked() as tx:
        await tx.mark_guest_minted(bid, NOW)

    await _purge(setup, bid)

    assert _renames(setup, bid) == [JobType.RENAME_GUEST]
    planned = next(j for j in setup.uow.state.jobs[bid] if j.type is JobType.RENAME_GUEST)
    assert (planned.run_at, planned.pinned_start, planned.channel) == (NOW, None, None)


async def test_a_booking_without_a_minted_guest_enqueues_nothing(setup: Setup) -> None:
    bid = await setup.booking()

    await _purge(setup, bid)

    assert _renames(setup, bid) == []


async def test_a_booking_purged_twice_enqueues_once(setup: Setup) -> None:
    bid = await setup.booking()
    async with setup.uow.locked() as tx:
        await tx.mark_guest_minted(bid, NOW)

    await _purge(setup, bid)
    await _purge(setup, bid)

    assert _renames(setup, bid) == [JobType.RENAME_GUEST]


async def test_the_first_moment_a_guest_was_minted_is_kept(setup: Setup) -> None:
    bid = await setup.booking()
    later = NOW.replace(hour=9)
    async with setup.uow.locked() as tx:
        await tx.mark_guest_minted(bid, NOW)
        await tx.mark_guest_minted(bid, later)

    assert setup.uow.state.bookings[bid].guest_minted_at == NOW


async def test_the_job_mints_again_under_the_neutral_term(setup: Setup) -> None:
    bid = await setup.booking()
    await _purge(setup, bid)
    queue = FakeQueue([job(bid, JobType.RENAME_GUEST, pinned=None)])

    await setup.runner(queue, guest_keys=lambda: [CURRENT]).run_due()

    assert setup.video.guests == [(guest_subject(bid, CURRENT), NEUTRAL_TERM)]
    assert queue.done == [1]
    assert queue.failed == [] == queue.retried


async def test_after_a_key_rotation_the_guest_is_renamed_under_both_keys(setup: Setup) -> None:
    bid = await setup.booking()
    await _purge(setup, bid)
    queue = FakeQueue([job(bid, JobType.RENAME_GUEST, pinned=None)])

    await setup.runner(queue, guest_keys=lambda: [CURRENT, PREVIOUS, CURRENT]).run_due()

    assert setup.video.guests == [
        (guest_subject(bid, CURRENT), NEUTRAL_TERM),
        (guest_subject(bid, PREVIOUS), NEUTRAL_TERM),
    ]


async def test_the_job_holds_the_booking_id_and_no_name_or_token(setup: Setup) -> None:
    bid = await setup.booking()
    async with setup.uow.locked() as tx:
        await tx.mark_guest_minted(bid, NOW)
    await _purge(setup, bid)

    planned = next(j for j in setup.uow.state.jobs[bid] if j.type is JobType.RENAME_GUEST)

    # A job is its type, its moment, an optional pinned start and an optional channel; the
    # booking's id is the row's own. Nothing else can be stored on it.
    assert {f for f in vars(planned)} == {"type", "run_at", "pinned_start", "channel"}


async def test_a_worker_without_keys_skips_the_job(setup: Setup) -> None:
    bid = await setup.booking()
    queue = FakeQueue([job(bid, JobType.RENAME_GUEST, pinned=None)])

    await setup.runner(queue).run_due()

    assert setup.video.guests == []
    assert queue.done == [1]
    assert [
        (e.action, e.reason) for e in setup.uow.state.audit if e.outcome is Outcome.SKIPPED
    ] == [("job.rename_guest", NO_GUEST_KEY)]


async def test_a_retryable_failure_is_retried_with_the_usual_backoff(setup: Setup) -> None:
    bid = await setup.booking()
    setup.video.guest_error = IntegrationError("server_error", retryable=True)
    queue = FakeQueue([job(bid, JobType.RENAME_GUEST, pinned=None)])

    await setup.runner(queue, guest_keys=lambda: [CURRENT]).run_due()

    assert queue.retried[0][1] == NOW + BACKOFF[0]
    assert queue.failed == []
    assert [e.outcome for e in setup.uow.state.audit if e.action == "job.rename_guest"] == [
        Outcome.RETRY
    ]


async def test_a_refusal_gives_up_at_once_with_one_system_event(setup: Setup) -> None:
    bid = await setup.booking()
    setup.video.guest_error = IntegrationError("rejected_403", retryable=False)
    queue = FakeQueue([job(bid, JobType.RENAME_GUEST, pinned=None)])

    await setup.runner(queue, guest_keys=lambda: [CURRENT]).run_due()

    assert queue.failed == [(1, "rejected_403")]
    gave_up = [e for e in setup.uow.state.audit if e.action == "job.rename_guest"]
    assert [(e.outcome, e.reason) for e in gave_up] == [(Outcome.FAILURE, "rejected_403")]
    # The code only: neither a name, a subject nor a token is in what is left behind.
    assert all(NEUTRAL_TERM not in f"{e}" for e in gave_up)


async def test_a_retry_that_runs_out_gives_up(setup: Setup) -> None:
    bid = await setup.booking()
    setup.video.guest_error = IntegrationError("server_error", retryable=True)
    last = job(bid, JobType.RENAME_GUEST, attempts=len(BACKOFF) + 1, pinned=None)
    queue = FakeQueue([last])

    await setup.runner(queue, guest_keys=lambda: [CURRENT]).run_due()

    assert queue.failed == [(1, "server_error")]
    assert queue.retried == []


async def test_the_job_is_claimed_by_a_worker_with_a_video_provider() -> None:
    with_video = Setup(group_uow(**WIRED)).runner(FakeQueue([]), guest_keys=lambda: [CURRENT])

    assert JobType.RENAME_GUEST in with_video.types
