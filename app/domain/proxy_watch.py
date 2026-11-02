"""Requests that carry forwarded headers from a peer the app does not trust (ADR 0086).

A proxy whose address is not in ``FORWARDED_ALLOW_IPS`` sets ``X-Forwarded-For`` and
``X-Forwarded-Proto``, and uvicorn then ignores both: every visitor shares the proxy's address
and the app builds ``http`` redirects (the tunnel trial of 1 November 2026). The worker's round
trip finds this on schedule; this watch finds it on the first real request, in the web process.

    request with forwarded headers, app kept the peer ─► observe ─► counted per process
                                                                 ─► due? ─► system event (codes)

It keeps counters and, for a peer in a private or loopback range (the proxy), its address. The
address of a public peer is never stored: that is a visitor. Nothing is persisted; a restart
starts again, and the system event, repeated at most once per repeat period, is the record.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.ports import Clock
from app.domain.public_address import PeerClass, classify_peer
from app.domain.tunable import value_of

EVENT_REASON = "proxy_headers_untrusted"
"""Reason of the system event ``public_address`` that this watch raises."""


@dataclass(frozen=True)
class ProxyHeaderSighting:
    """What the watch has seen since the process started.

    Attributes:
        count: Requests with forwarded headers that the app did not honour.
        first_seen: When the first one arrived (UTC).
        last_seen: When the latest one arrived (UTC).
        peer_class: The class of the latest peer.
        peer: The latest peer's address when it is loopback or private (the proxy); ``None``
            for a public peer, which is a visitor.
    """

    count: int
    first_seen: datetime
    last_seen: datetime
    peer_class: PeerClass
    peer: str | None


class ProxyHeaderWatch:
    """Counts untrusted forwarded headers and says when an event is due.

    Args:
        clock: Current time.
        repeat: How often a lasting problem is reported again (``tls_alert_repeat_hours``).
    """

    def __init__(self, clock: Clock, repeat: timedelta | Callable[[], timedelta]) -> None:
        """Start with nothing seen."""
        self._clock = clock
        self._repeat = repeat
        self._sighting: ProxyHeaderSighting | None = None
        self._reported: datetime | None = None

    def observe(self, peer: str | None) -> bool:
        """Count one request and return whether a system event is due for it.

        Args:
            peer: The address the connection came from, as the app sees it.
        """
        now = self._clock.now()
        peer_class = classify_peer(peer)
        known = peer if peer_class is not PeerClass.PUBLIC else None
        previous = self._sighting
        self._sighting = ProxyHeaderSighting(
            (previous.count if previous else 0) + 1,
            previous.first_seen if previous else now,
            now,
            peer_class,
            known,
        )
        if self._reported is not None and now - self._reported < value_of(self._repeat):
            return False
        self._reported = now
        return True

    def sighting(self) -> ProxyHeaderSighting | None:
        """Return what has been seen, or ``None`` when nothing yet."""
        return self._sighting
