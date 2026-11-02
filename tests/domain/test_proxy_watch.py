"""The watch on forwarded headers from a peer the app does not trust (ADR 0086)."""

from datetime import timedelta

from app.domain.proxy_watch import ProxyHeaderWatch
from app.domain.public_address import PeerClass
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW


def test_nothing_is_seen_until_a_request_is_ignored() -> None:
    assert ProxyHeaderWatch(FixedClock(NOW), timedelta(hours=24)).sighting() is None


def test_a_private_peer_is_counted_with_its_address_a_public_one_without() -> None:
    watch = ProxyHeaderWatch(FixedClock(NOW), timedelta(hours=24))

    watch.observe("172.18.0.1")
    watch.observe("172.18.0.1")
    private = watch.sighting()
    watch.observe("8.8.8.8")
    public = watch.sighting()

    assert private is not None
    assert (private.count, private.peer_class, private.peer) == (2, PeerClass.PRIVATE, "172.18.0.1")
    assert public is not None
    assert (public.count, public.peer_class, public.peer) == (3, PeerClass.PUBLIC, None)
    assert public.first_seen == private.first_seen


def test_an_event_is_due_the_first_time_and_then_once_per_repeat_period() -> None:
    clock = FixedClock(NOW)
    watch = ProxyHeaderWatch(clock, lambda: timedelta(hours=24))

    assert watch.observe("172.18.0.1") is True
    assert watch.observe("172.18.0.1") is False
    clock.advance(timedelta(hours=23))
    assert watch.observe("172.18.0.1") is False
    clock.advance(timedelta(hours=2))
    assert watch.observe("172.18.0.1") is True
