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
refused: a stranger must not be able to make the platform write. Only the alert, which a stranger
cannot cause alone, is written (``write_alert``).

What it holds is bounded and holds nothing personal. An entry exists only for an address that
exists (a platform and an organisation's slug, or the installation); calls to an address that does
not exist count under their platform, never per guessed slug. At most ``max_entries`` entries are
kept; beyond that the ones whose window ended are dropped first, then the oldest. An entry holds
codes (outcomes and reasons from closed lists), counts and times: no body, header, signature,
address or pseudonym.

A window starts with the first call to an address after the previous window ended and lasts as
long as the security log's (``security_event_window_minutes``). The counts start again with the
next window; the last accepted and last refused times stay.

**The alert rule** (``counts_toward_alert``) counts what a passer-by cannot cause alone, because
anyone who knows a slug can send calls that fail: a ``stale`` delivery (it was signed), any
refusal from inside the connection's own list of networks, a refusal for the network while a
delivery was verified earlier in the same window (the list may have gone stale, point 3), and an
event about work this connection did not create (point 12). When their number passes
``webhook_refusal_alert_count`` the ledger returns one ``Alert`` for the window; ``write_alert``
turns it into one system event for the organisation.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.inbound_gate import Refusal
from app.domain.ports import Clock, UnitOfWork
from app.domain.tunable import value_of
from app.domain.webhook_sources import ChainFault

ALERT_ACTION = "webhook.refusal_alert"
"""The system event an address that passed the threshold raises, once per window."""

ALERT_REASON = "too_many_refusals"
"""Reason of ``webhook.refusal_alert``."""


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


def counts_toward_alert(
    reason: str,
    *,
    inside_list: bool = False,
    delivered_earlier: bool = False,
    organisational: bool = True,
) -> bool:
    """Return whether an outcome joins the alert (ADR 0124 points 3, 10 and 12).

    Args:
        reason: A refusal's code, or ``unknown_work``.
        inside_list: The call came from inside the connection's own list of networks (a list
            applies and the source check passed).
        delivered_earlier: A call was verified for this address earlier in the same window.
        organisational: The address is an organisation's own. An address of the installation is
            shared by every tenant of the platform, so an event about work that is not ours is
            routine there and never counts.
    """
    if reason == Delivered.UNKNOWN_WORK.value:
        return organisational
    if reason == Refusal.STALE.value:
        return True
    if reason in {r.value for r in Refusal}:
        return inside_list or (reason == Refusal.NETWORK.value and delivered_earlier)
    return False


@dataclass(frozen=True)
class Alert:
    """An address that passed the threshold in this window; the system event says exactly this.

    Attributes:
        key: The address.
        count: How many outcomes were counted when it passed.
        reasons: The codes of what was counted, sorted; nothing else.
    """

    key: AddressKey
    count: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class LedgerView:
    """One address as the card shows it: the window's counts and the last times.

    Attributes:
        window_started: When the current window began, or ``None`` when no call came in it yet.
        outcomes: Calls per outcome code in the window (``Delivered`` and ``Refusal`` values).
        faults: The faults of single calls (the caller is the proxy, ...) by kind, in the window.
        inside_list: Refusals in the window that came from inside the connection's own list.
        counted: How many outcomes the alert counted in the window.
        last_accepted: When a delivery was last accepted, across windows.
        last_refused_at: When a call was last refused, across windows.
        last_refused_reason: The code of that refusal.
    """

    window_started: datetime | None
    outcomes: dict[str, int]
    faults: dict[ChainFault, int]
    inside_list: int
    counted: int
    last_accepted: datetime | None
    last_refused_at: datetime | None
    last_refused_reason: str | None

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
    counted: dict[str, int] = field(default_factory=dict)
    inside_list: int = 0
    verified: bool = False
    alerted: bool = False
    last_accepted: datetime | None = None
    last_refused: tuple[datetime, str] | None = None

    def start_window(self, now: datetime) -> None:
        """Begin a new window: the counts start again, the last times stay."""
        self.started = now
        self.outcomes, self.faults, self.counted = {}, {}, {}
        self.inside_list = 0
        self.verified = self.alerted = False


class WebhookLedger:
    """Remembers what the gate saw per address and says when the alert is due.

    Args:
        clock: Current time.
        window: How long counts are kept together (``security_event_window_minutes``).
        alert_count: The number of counted outcomes the alert needs to pass
            (``webhook_refusal_alert_count``); read at each call, so a portal change applies.
        max_entries: How many addresses are kept at most.
    """

    def __init__(
        self,
        clock: Clock,
        window: timedelta | Callable[[], timedelta],
        alert_count: int | Callable[[], int],
        *,
        max_entries: int | Callable[[], int] = 1000,
    ) -> None:
        """Store the collaborators; no address has been seen yet."""
        self._clock = clock
        self._window = window
        self._alert_count = alert_count
        self._max_entries = max_entries
        self._entries: dict[AddressKey, _Entry] = {}

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
        self._count(entry, outcome.value)
        entry.verified = True
        if outcome is Delivered.ACCEPTED:
            entry.last_accepted = now
        if counts_toward_alert(outcome.value, organisational=key.organisation is not None):
            return self._maybe_alert(key, entry, outcome.value)
        return None

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
        self._count(entry, reason.value)
        entry.last_refused = (now, reason.value)
        if fault is not None:
            entry.faults[fault] = entry.faults.get(fault, 0) + 1
        if inside_list:
            entry.inside_list += 1
        if counts_toward_alert(
            reason.value, inside_list=inside_list, delivered_earlier=entry.verified
        ):
            return self._maybe_alert(key, entry, reason.value)
        return None

    def view(self, key: AddressKey) -> LedgerView | None:
        """Return what the ledger knows of an address, or ``None`` when it never saw it.

        The counts are the current window's: in a window without calls they are empty, while the
        last times stay.
        """
        entry = self._entries.get(key)
        if entry is None:
            return None
        live = self._clock.now() - entry.started < value_of(self._window)
        return LedgerView(
            entry.started if live else None,
            dict(entry.outcomes) if live else {},
            dict(entry.faults) if live else {},
            entry.inside_list if live else 0,
            sum(entry.counted.values()) if live else 0,
            entry.last_accepted,
            entry.last_refused[0] if entry.last_refused else None,
            entry.last_refused[1] if entry.last_refused else None,
        )

    def _entry(self, key: AddressKey, now: datetime) -> _Entry:
        """Return the address's entry in its current window at ``now``; make room for a new one."""
        entry = self._entries.get(key)
        if entry is None:
            self._make_room(now)
            entry = self._entries[key] = _Entry(now)
        elif now - entry.started >= value_of(self._window):
            entry.start_window(now)
        return entry

    def _make_room(self, now: datetime) -> None:
        """Keep the ledger within its bound: windows that ended go first, then the oldest."""
        limit = value_of(self._max_entries)
        if len(self._entries) < limit:
            return
        window = value_of(self._window)
        for stale in [k for k, e in self._entries.items() if now - e.started >= window]:
            del self._entries[stale]
        while len(self._entries) >= limit:
            oldest = min(self._entries, key=lambda k: self._entries[k].started)
            del self._entries[oldest]

    @staticmethod
    def _count(entry: _Entry, code: str) -> None:
        """Add one to the window's count of ``code``."""
        entry.outcomes[code] = entry.outcomes.get(code, 0) + 1

    def _maybe_alert(self, key: AddressKey, entry: _Entry, code: str) -> Alert | None:
        """Count a call towards the alert and return the alert once the threshold is passed."""
        entry.counted[code] = entry.counted.get(code, 0) + 1
        total = sum(entry.counted.values())
        if entry.alerted or total <= value_of(self._alert_count):
            return None
        entry.alerted = True
        return Alert(key, total, tuple(sorted(entry.counted)))


async def write_alert(uow: UnitOfWork, alert: Alert, at: datetime) -> None:
    """Write the alert as one system event for the organisation.

    The detail carries the platform's code, the count and the reasons' codes; nothing from any
    call. An address of the installation names no organisation, so only the superuser sees it.

    Args:
        uow: Opens the database transaction.
        alert: What the ledger said.
        at: When it happened.
    """
    detail = f"platform:{alert.key.platform},count:{alert.count},reasons:{'+'.join(alert.reasons)}"
    async with uow.locked() as tx:
        await tx.add_audit_event(
            AuditEvent(
                at,
                SYSTEM_ACTOR,
                ALERT_ACTION,
                Outcome.FAILURE,
                ALERT_REASON,
                alert.key.organisation,
                None,
                detail,
                Source.SYSTEM,
            )
        )
