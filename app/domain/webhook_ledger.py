"""What the inbound gate saw per address, and when that is worth an alert (ADR 0124).

The gate decides every outcome of a call to a webhook address; the ledger is *told* what it
decided and remembers it, so the connection's card can say when a delivery was last accepted and
last refused, how many calls ended in each way during the window, and the alert can fire. It never
decides an outcome:

    gate ─► refused(key, reason, inside_list, fault) ─┐
    route ─► delivered(key, accepted | duplicate | …) ─┴─► counts, last times ─► ``Alert``?
    card, status screens ─► view(key) ─► ``LedgerView``

**It lives in memory, in the web process**, like the security log and the rate limiter. A restart
empties it, and each process keeps its own: what the card shows is what *this* process has seen
since the window began (the card says so). Nothing is written to the database when a call is
refused: a stranger must not be able to make the platform write. Only the alert is written, by
``app.web.webhook_ledger`` (the web side owns the unit of work, as ``app.web.security_events``).

What it holds is bounded and holds nothing personal. An entry exists only for an address that
exists (a platform and an organisation's slug, or the installation); calls to an address that does
not exist count under their platform, never per guessed slug. At most ``max_entries`` entries are
kept. Making room never erases a live alert state: entries whose window ended go first, then the
oldest entry that has not alerted in its window; when every entry is inside its window and has
alerted, the new address is not recorded (``on_dropped`` is told its platform). An entry holds
codes (outcomes and reasons from closed lists), counts and times: no body, header, signature,
address or pseudonym.

A window starts with the first call to an address after the previous window ended and lasts as
long as the security log's (``security_event_window_minutes``). The counts start again with the
next window; the last accepted and last refused times stay.

**Two alerts per address and window, one per group** (``alert_group``), each with the threshold
``webhook_refusal_alert_count``, so noise cannot hide a signal:

- ``list``: a refusal for the network while a delivery was verified earlier in the same window
  (point 3: the list may have gone stale). A stranger who knows the address can cause this while
  real deliveries arrive; it costs one event per window and cannot hide the other alert.
- ``signed``: what a passer-by cannot cause alone: a ``stale`` delivery (it was signed), any
  refusal from inside the connection's own list of networks, and an event about work this
  connection did not create (point 12, an organisation's own address only).

When a group passes the threshold the ledger returns one ``Alert`` for that group and window. If
the event could not be written, ``retract`` takes the mark back: the next counted outcome after
``RETRACT_PAUSE``, still inside the window, raises it again. While the ledger is full, the
platform of a dropped address is reported once per platform and window, not once per call.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.inbound_gate import Refusal
from app.domain.ports import Clock
from app.domain.tunable import value_of
from app.domain.webhook_sources import ChainFault

RETRACT_PAUSE = timedelta(seconds=60)
"""How long a group whose alert could not be written is not raised again. While the database
stays locked, every counted outcome would otherwise try the write and wait out the lock; this is
a protection of our own write path, so it is a constant and not a setting (ADR 0042)."""


class Delivered(StrEnum):
    """What came of a call that passed every check of the gate (the closed list)."""

    ACCEPTED = "accepted"
    """Verified and handled (recorded, or an event the timeline does not show)."""
    DUPLICATE = "duplicate"
    """Verified, and the same event was recorded before (a replay or a redelivery)."""
    PAUSED = "paused"
    """Verified at a paused address and dropped (point 9)."""
    UNKNOWN_WORK = "unknown_work"
    """Verified, about work this connection did not create (point 12); answered 204."""


class AlertGroup(StrEnum):
    """The two kinds of outcome the alert counts, each with its own count and its own alert."""

    LIST = "list"
    """A refusal for the network after a verified delivery: a stale list, or a stranger."""
    SIGNED = "signed"
    """Signed or from inside the list, or unknown work: what a passer-by cannot cause alone."""


@dataclass(frozen=True)
class AddressKey:
    """The address an entry is about.

    Attributes:
        platform: The platform's code (``webex_cc``, ``webex``), fixed by the application.
        organisation: The organisation's slug for an organisation's own address; ``None`` for an
            address of the installation.
        unknown: Calls to an address that does not exist, counted together under the platform:
            never per organisation, so a guessed slug costs nothing.
    """

    platform: str
    organisation: str | None = None
    unknown: bool = False


def alert_group(
    outcome: Refusal | Delivered,
    *,
    inside_list: bool = False,
    delivered_earlier: bool = False,
    organisational: bool = True,
) -> AlertGroup | None:
    """Return the group an outcome is counted in for the alert, or ``None`` (ADR 0124 10, 12).

    Args:
        outcome: A refusal's reason, or what came of a verified call.
        inside_list: The call came from inside the connection's own list of networks (a list
            applies and the source check passed).
        delivered_earlier: A call was verified for this address earlier in the same window.
        organisational: The address is an organisation's own. An address of the installation is
            shared by every tenant of the platform, so an event about work that is not ours is
            routine there and never counts.
    """
    if outcome is Delivered.UNKNOWN_WORK:
        return AlertGroup.SIGNED if organisational else None
    if not isinstance(outcome, Refusal):
        return None
    if outcome is Refusal.STALE or inside_list:
        return AlertGroup.SIGNED
    if outcome is Refusal.NETWORK and delivered_earlier:
        return AlertGroup.LIST
    return None


@dataclass(frozen=True)
class Alert:
    """An address whose group passed the threshold in this window; the event says exactly this.

    Attributes:
        key: The address.
        group: Which group of outcomes passed it.
        count: How many outcomes of the group were counted when it passed.
        reasons: The codes of what the group counted, sorted; nothing else.
        started: When the window it belongs to began (so ``retract`` never touches a later one).
    """

    key: AddressKey
    group: AlertGroup
    count: int
    reasons: tuple[str, ...]
    started: datetime


@dataclass(frozen=True)
class LedgerView:
    """One address as the card shows it: the window's counts and the last times.

    Attributes:
        window_started: When the current window began, or ``None`` when no call came in it yet.
        outcomes: Calls per outcome code in the window (``Delivered`` and ``Refusal`` values).
        faults: The faults of single calls (the caller is the proxy, ...) by kind, in the window.
        inside_list: Refusals in the window that came from inside the connection's own list.
        counted: How many outcomes the alert counted in the window, over both groups.
        last_accepted: When a delivery was last accepted, across windows.
        last_refused_at: When a call was last refused, across windows.
        last_refused_reason: The code of that refusal.
        by_previous: How many of the window's verified deliveries the previous secret verified
            (ADR 0124 point 8): a count of its own, not an outcome, so the rows of outcomes still
            add up to the calls.
    """

    window_started: datetime | None
    outcomes: dict[str, int]
    faults: dict[ChainFault, int]
    inside_list: int
    counted: int
    last_accepted: datetime | None
    last_refused_at: datetime | None
    last_refused_reason: str | None
    by_previous: int = 0

    @property
    def paused_dropped(self) -> int:
        """How many verified events were dropped while the address was paused, in the window."""
        return self.outcomes.get(Delivered.PAUSED.value, 0)


@dataclass
class _Entry:
    """The state of one address: the current window's counts and the last times."""

    started: datetime
    outcomes: dict[str, int] = field(default_factory=dict)
    faults: dict[ChainFault, int] = field(default_factory=dict)
    counted: dict[AlertGroup, dict[str, int]] = field(default_factory=dict)
    inside_list: int = 0
    by_previous: int = 0
    verified: bool = False
    alerted: set[AlertGroup] = field(default_factory=set)
    retracted: dict[AlertGroup, datetime] = field(default_factory=dict)
    last_accepted: datetime | None = None
    last_refused: tuple[datetime, str] | None = None

    def start_window(self, now: datetime) -> None:
        """Begin a new window: the counts start again, the last times stay."""
        self.started = now
        self.outcomes, self.faults, self.counted = {}, {}, {}
        self.inside_list = 0
        self.by_previous = 0
        self.verified = False
        self.alerted = set()
        self.retracted = {}


class WebhookLedger:
    """Remembers what the gate saw per address and says when the alert is due.

    Args:
        clock: Current time.
        window: How long counts are kept together (``security_event_window_minutes``).
        alert_count: The number of counted outcomes a group needs to pass
            (``webhook_refusal_alert_count``); read at each call, so a portal change applies.
        max_entries: How many addresses are kept at most.
        on_dropped: Told the platform's code when a new address could not be recorded because
            every entry is inside its window and has alerted.
    """

    def __init__(
        self,
        clock: Clock,
        window: timedelta | Callable[[], timedelta],
        alert_count: int | Callable[[], int],
        *,
        max_entries: int | Callable[[], int] = 1000,
        on_dropped: Callable[[str], None] = lambda platform: None,
    ) -> None:
        """Store the collaborators; no address has been seen yet."""
        self._clock = clock
        self._window = window
        self._alert_count = alert_count
        self._max_entries = max_entries
        self._on_dropped = on_dropped
        self._entries: dict[AddressKey, _Entry] = {}
        self._dropped_at: dict[str, datetime] = {}

    @property
    def size(self) -> int:
        """How many addresses the ledger holds (the bound is ``max_entries``)."""
        return len(self._entries)

    def delivered(self, key: AddressKey, outcome: Delivered) -> Alert | None:
        """Note a call that passed every check; return the alert if this one is the one.

        Args:
            key: The address.
            outcome: What came of the call.
        """
        now = self._clock.now()
        entry = self._entry(key, now)
        if entry is None:
            return None
        self._count(entry, outcome.value)
        entry.verified = True
        if outcome is Delivered.ACCEPTED:
            entry.last_accepted = now
        group = alert_group(outcome, organisational=key.organisation is not None)
        return self._maybe_alert(key, entry, group, outcome.value)

    def verified_by_previous(self, key: AddressKey) -> None:
        """Count a call that verified under the previous secret (point 8), and nothing else.

        It counts beside the call's outcome (``delivered`` is told that too), never instead of it:
        no alert, no last-accepted time, no effect on the window.
        """
        entry = self._entry(key, self._clock.now())
        if entry is not None:
            entry.by_previous += 1

    def refused(
        self,
        key: AddressKey,
        reason: Refusal,
        *,
        inside_list: bool = False,
        fault: ChainFault | None = None,
    ) -> Alert | None:
        """Note a refused call; return the alert if this one is the one.

        Args:
            key: The address; one that does not exist is ``AddressKey(platform, unknown=True)``.
            reason: Why it was refused.
            inside_list: The call came from inside the connection's own list of networks.
            fault: The per-call fault of the proxy chain behind a ``source_unknown`` refusal.
        """
        now = self._clock.now()
        entry = self._entry(key, now)
        if entry is None:
            return None
        self._count(entry, reason.value)
        entry.last_refused = (now, reason.value)
        if fault is not None:
            entry.faults[fault] = entry.faults.get(fault, 0) + 1
        if inside_list:
            entry.inside_list += 1
        group = alert_group(reason, inside_list=inside_list, delivered_earlier=entry.verified)
        return self._maybe_alert(key, entry, group, reason.value)

    def retract(self, alert: Alert) -> None:
        """Take back the mark of an alert whose event could not be written.

        The next counted outcome of its group then raises it again. Nothing happens when the
        window has moved on or the entry is gone.
        """
        entry = self._entries.get(alert.key)
        if entry is not None and entry.started == alert.started:
            entry.alerted.discard(alert.group)
            entry.retracted[alert.group] = self._clock.now()

    def view(self, key: AddressKey) -> LedgerView | None:
        """Return what the ledger knows of an address, or ``None`` when it never saw it.

        The counts are the current window's: in a window without calls they are empty, while the
        last times stay.
        """
        entry = self._entries.get(key)
        if entry is None:
            return None
        live = self._clock.now() - entry.started < value_of(self._window)
        counted = sum(sum(codes.values()) for codes in entry.counted.values())
        return LedgerView(
            entry.started if live else None,
            dict(entry.outcomes) if live else {},
            dict(entry.faults) if live else {},
            entry.inside_list if live else 0,
            counted if live else 0,
            entry.last_accepted,
            entry.last_refused[0] if entry.last_refused else None,
            entry.last_refused[1] if entry.last_refused else None,
            entry.by_previous if live else 0,
        )

    def _entry(self, key: AddressKey, now: datetime) -> _Entry | None:
        """Return the address's entry in its current window at ``now``, or ``None`` if full.

        A new address needs room; when none can be made without erasing a live alert state it is
        not recorded and ``on_dropped`` is told.
        """
        entry = self._entries.get(key)
        if entry is None:
            if not self._make_room(now):
                self._report_dropped(key.platform, now)
                return None
            entry = self._entries[key] = _Entry(now)
        elif now - entry.started >= value_of(self._window):
            entry.start_window(now)
        return entry

    def _report_dropped(self, platform: str, now: datetime) -> None:
        """Tell ``on_dropped`` once per platform and window, however many calls were dropped."""
        last = self._dropped_at.get(platform)
        if last is None or now - last >= value_of(self._window):
            self._dropped_at[platform] = now
            self._on_dropped(platform)

    def _make_room(self, now: datetime) -> bool:
        """Keep the ledger within its bound without erasing a live alert state.

        Entries whose window ended go first, then the oldest entry that has not alerted in its
        window. Returns ``False`` when every entry is inside its window and has alerted.
        """
        limit = value_of(self._max_entries)
        if len(self._entries) < limit:
            return True
        window = value_of(self._window)
        for ended in [k for k, e in self._entries.items() if now - e.started >= window]:
            del self._entries[ended]
        while len(self._entries) >= limit:
            quiet = [k for k, e in self._entries.items() if not e.alerted]
            if not quiet:
                return False
            del self._entries[min(quiet, key=lambda k: self._entries[k].started)]
        return True

    @staticmethod
    def _count(entry: _Entry, code: str) -> None:
        """Add one to the window's count of ``code``."""
        entry.outcomes[code] = entry.outcomes.get(code, 0) + 1

    def _maybe_alert(
        self, key: AddressKey, entry: _Entry, group: AlertGroup | None, code: str
    ) -> Alert | None:
        """Count a call in its group and return the group's alert once its threshold is passed."""
        if group is None:
            return None
        codes = entry.counted.setdefault(group, {})
        codes[code] = codes.get(code, 0) + 1
        total = sum(codes.values())
        if group in entry.alerted or total <= value_of(self._alert_count):
            return None
        paused_since = entry.retracted.get(group)
        if paused_since is not None and self._clock.now() - paused_since < RETRACT_PAUSE:
            return None
        entry.alerted.add(group)
        return Alert(key, group, total, tuple(sorted(codes)), entry.started)
