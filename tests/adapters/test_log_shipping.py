"""The senders of the log destinations: filter, queue, batch, back off, report (ADR 0085)."""

import asyncio
import io
import json
from dataclasses import replace
from datetime import timedelta

import pytest
import structlog

from app.adapters.db.log_destinations import LogTarget
from app.adapters.log_shipping import LogShipper, Pipeline, ShipSettings, probe_line
from app.domain.audit import Outcome
from app.domain.log_destinations import HEALTH_EVENT, Reason, State
from app.logging import configure_logging, set_line_tap
from tests.domain.test_log_destinations import destination
from tests.fakes.clock import FixedClock
from tests.fakes.log_destinations import FakeFactory, FakeSender, FakeTargets
from tests.fakes.persistence import InMemoryUnitOfWork

SETTINGS = ShipSettings(batch_size=3, flush_seconds=0.01, queue_size=5, backoff_max_seconds=4)


def line(message: str = "m", level: str = "error", **fields: object) -> str:
    return json.dumps({"log.level": level, "message": message, **fields})


def pipeline(sender: FakeSender | None, **changes: object) -> Pipeline:
    target = LogTarget(destination(min_level="info", **changes), "token")
    return Pipeline(target, sender, "web", FixedClock(), lambda: SETTINGS)


def offer(p: Pipeline, text: str) -> None:
    p.offer(text, json.loads(text))


# --- the queue ---------------------------------------------------------------------------


def test_only_accepted_lines_are_queued() -> None:
    p = pipeline(FakeSender())

    offer(p, line("kept", "warning"))
    offer(p, line("too quiet", "debug"))
    offer(p, line("trace", "trace"))
    offer(p, line("audit", "info", log_type="audit", **{"event.category": "audit"}))
    offer(p, line("system", "info", log_type="audit", **{"event.category": "system"}))

    assert p.status().queued == 2  # the warning and the audit line; system is not ticked


def test_a_full_queue_drops_the_oldest_line_and_counts_it() -> None:
    p = pipeline(FakeSender())

    for n in range(8):
        offer(p, line(f"m{n}"))

    status = p.status()
    assert (status.queued, status.dropped) == (5, 3)


def test_a_runaway_line_is_not_queued() -> None:
    p = pipeline(FakeSender())

    offer(p, line("x" * 70_000))

    assert p.status().queued == 0


async def test_lines_are_sent_in_order_in_batches_of_the_configured_size() -> None:
    sender = FakeSender()
    p = pipeline(sender)
    for n in range(5):
        offer(p, line(f"m{n}"))

    assert await p.flush_once() is State.OK
    assert await p.flush_once() is State.OK
    assert await p.flush_once() is None  # nothing left

    assert [len(b) for b in sender.batches] == [3, 2]
    assert [json.loads(x)["message"] for x in sender.lines] == ["m0", "m1", "m2", "m3", "m4"]
    status = p.status()
    assert (status.state, status.sent, status.queued, status.failed) == (State.OK, 5, 0, 0)
    assert status.last_ok_at == FixedClock().now()


# --- failing and backing off --------------------------------------------------------------


async def test_a_failed_batch_is_kept_and_sent_first_when_the_platform_is_back() -> None:
    sender = FakeSender()
    sender.failures = [Reason.UNREACHABLE, Reason.UNREACHABLE]
    p = pipeline(sender)
    for n in range(4):
        offer(p, line(f"m{n}"))

    assert await p.flush_once() is State.FAILING
    offer(p, line("late"))
    assert await p.flush_once() is State.FAILING
    assert await p.flush_once() is State.OK
    assert await p.flush_once() is State.OK

    assert [json.loads(x)["message"] for x in sender.lines] == ["m0", "m1", "m2", "m3", "late"]
    status = p.status()
    assert (status.failed, status.sent, status.last_error, status.state) == (
        2,
        5,
        "unreachable",
        State.OK,
    )


async def test_the_wait_doubles_up_to_the_maximum_and_resets_on_success() -> None:
    sender = FakeSender()
    sender.failures = [Reason.REFUSED] * 5
    p = pipeline(sender)
    offer(p, line())

    waits = []
    for _ in range(5):
        await p.flush_once()
        waits.append(p.delay())
    await p.flush_once()

    assert waits == [1.0, 2.0, 4.0, 4.0, 4.0]  # capped at backoff_max_seconds
    assert p.delay() == SETTINGS.flush_seconds


def test_a_full_batch_is_sent_without_waiting() -> None:
    p = pipeline(FakeSender())
    for n in range(3):
        offer(p, line(f"m{n}"))

    assert p.delay() == 0.0


async def test_a_destination_whose_credential_cannot_be_opened_never_sends() -> None:
    p = pipeline(None)
    offer(p, line())

    assert await p.flush_once() is None
    status = p.status()
    assert (status.state, status.last_error, status.queued) == (State.FAILING, "not_ready", 0)


# --- the shipper --------------------------------------------------------------------------


class Env:
    def __init__(self) -> None:
        self.uow = InMemoryUnitOfWork()
        self.clock = FixedClock()
        self.source = FakeTargets()
        self.factory = FakeFactory()
        self.shipper = LogShipper(
            self.source,
            self.factory,
            self.uow,
            self.clock,
            "web",
            lambda: SETTINGS,
            window=lambda: timedelta(minutes=15),
        )

    def target(self, destination_id: int = 1, **changes: object) -> LogTarget:
        return LogTarget(
            destination(id=destination_id, name=f"D{destination_id}", **changes), "tok"
        )

    def events(self) -> list[tuple[str, Outcome, str, str]]:
        return [
            (e.action, e.outcome, e.reason, e.detail)
            for e in self.uow.state.audit
            if e.action == HEALTH_EVENT
        ]


@pytest.fixture
async def env():
    env = Env()
    yield env
    await env.shipper.aclose()


async def test_reload_starts_stops_and_rebuilds_pipelines(env: Env) -> None:
    env.source.targets = [env.target(1), env.target(2)]
    await env.shipper.reload()
    assert sorted(env.shipper.pipelines) == [1, 2]
    assert len(env.factory.built) == 2

    await env.shipper.reload()  # nothing changed: nothing rebuilt
    assert len(env.factory.built) == 2

    env.source.targets = [env.target(1, min_level="error")]  # 1 changed, 2 switched off
    await env.shipper.reload()
    assert sorted(env.shipper.pipelines) == [1]
    assert len(env.factory.built) == 3
    assert env.factory.built[1][1].closed  # the sender of 2 was closed
    assert env.factory.built[0][1].closed  # and the old sender of 1


async def test_a_new_credential_rebuilds_the_pipeline(env: Env) -> None:
    env.source.targets = [env.target(1)]
    await env.shipper.reload()

    env.source.targets = [replace(env.target(1), secret="rotated")]
    await env.shipper.reload()

    assert len(env.factory.built) == 2


async def test_a_failed_read_keeps_the_pipelines(env: Env) -> None:
    env.source.targets = [env.target(1)]
    await env.shipper.reload()

    env.source.fail = True
    await env.shipper.reload()

    assert sorted(env.shipper.pipelines) == [1]


async def test_shipping_off_stops_every_pipeline() -> None:
    env = Env()
    on = [True]
    shipper = LogShipper(
        env.source, env.factory, env.uow, env.clock, "web", lambda: SETTINGS, lambda: on[0]
    )
    env.source.targets = [env.target(1)]
    await shipper.reload()
    assert shipper.pipelines
    on[0] = False

    await shipper.reload()
    shipper.offer(line())

    assert shipper.pipelines == {}


async def test_the_tap_feeds_every_matching_destination_and_ignores_garbage(env: Env) -> None:
    env.source.targets = [env.target(1, min_level="error"), env.target(2, min_level="debug")]
    await env.shipper.reload()

    env.shipper.offer(line("loud", "error"))
    env.shipper.offer(line("quiet", "debug"))
    env.shipper.offer("not json at all")
    env.shipper.offer("[1, 2]")

    assert [p.status().queued for p in env.shipper.pipelines.values()] == [1, 2]


async def test_delivery_runs_in_the_background_and_a_failure_is_one_event_then_one_recovery(
    env: Env,
) -> None:
    env.source.targets = [env.target(1)]
    await env.shipper.reload()
    sender = env.factory.last
    sender.failures = [Reason.TLS, Reason.TLS]

    env.shipper.offer(line("a", "error"))
    for _ in range(300):
        await asyncio.sleep(0.01)
        if sender.lines:
            break

    assert [json.loads(x)["message"] for x in sender.lines] == ["a"]
    assert env.events() == [
        (HEALTH_EVENT, Outcome.FAILURE, "tls", "splunk_hec:1"),
        (HEALTH_EVENT, Outcome.SUCCESS, "", "splunk_hec:1"),
    ]


async def test_a_flapping_destination_does_not_fill_the_audit_log(env: Env) -> None:
    env.source.targets = [env.target(1)]
    await env.shipper.reload()
    pipe = env.shipper.pipelines[1]

    for _ in range(3):
        await env.shipper._report(1, pipe, State.FAILING)  # noqa: SLF001
        await env.shipper._report(1, pipe, State.OK)  # noqa: SLF001

    assert [e[1] for e in env.events()] == [Outcome.FAILURE, Outcome.SUCCESS]  # inside the window

    env.clock.advance(timedelta(minutes=16))
    await env.shipper._report(1, pipe, State.FAILING)  # noqa: SLF001
    assert [e[1] for e in env.events()][-1] is Outcome.FAILURE


async def test_the_status_is_stored_once_and_again_only_when_it_changed(env: Env) -> None:
    env.source.targets = [env.target(1)]
    await env.shipper.reload()
    pipe = env.shipper.pipelines[1]
    env.uow.state.log_destinations[1] = destination(id=1)

    await env.shipper.store_status()
    first = env.uow.state.log_status[(1, "web")]
    env.uow.state.log_status.clear()
    await env.shipper.store_status()  # unchanged since the last store

    assert first.state is State.IDLE
    assert env.uow.state.log_status == {}
    pipe.changed = True
    await env.shipper.store_status()
    assert (1, "web") in env.uow.state.log_status


# --- the line tap ------------------------------------------------------------------------


@pytest.fixture
def tapped():
    lines: list[str] = []
    set_line_tap(lines.append)
    yield lines
    set_line_tap(None)
    structlog.reset_defaults()


def test_the_tap_sees_exactly_the_masked_lines_stdout_sees(tapped: list[str]) -> None:
    out = io.StringIO()
    configure_logging("INFO", stream=out)

    structlog.get_logger().info("booking.created", booking_id=7, email="a@example.org")
    structlog.get_logger().debug("below the level")

    assert tapped == out.getvalue().splitlines()
    assert len(tapped) == 1
    assert "a@example.org" not in tapped[0]
    assert json.loads(tapped[0])["email"] == "***"


def test_a_failing_tap_never_breaks_logging() -> None:
    def broken(_: str) -> None:
        raise RuntimeError("tap down")

    out = io.StringIO()
    configure_logging("INFO", stream=out)
    set_line_tap(broken)
    try:
        structlog.get_logger().info("still logged")
    finally:
        set_line_tap(None)
        structlog.reset_defaults()

    assert "still logged" in out.getvalue()


def test_the_test_line_names_the_destination_and_carries_no_address() -> None:
    text = probe_line(FixedClock(), "web", destination(id=4))

    record = json.loads(text)
    assert (record["message"], record["destination"], record["service.name"]) == (
        "logging.test",
        4,
        "web",
    )
    assert "splunk.example.org" not in text
