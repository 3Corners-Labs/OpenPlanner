"""The rules of immediate intake: admission, the participant's view and access (ADR 0121 step 7).

Immediate work is a request without an appointment: a participant asks for help *now* under a
flow whose intake modes include ``on_demand``. Waiting is not treatment (ADR 0122 invariant 4):
a waiting request holds no unit of its pool; the claim acquires one (``check_start`` with the
intake ``on_demand``, under the lock). Admission therefore asks two questions only:

    max_waiting reached? ─► FULL
    earliest_start(supply, commitments, now, length, wrap-up, now + look-ahead) is None?
                         ─► UNAVAILABLE (nobody staffed for it, or no start that fits in time)
    otherwise            ─► ACCEPTED, with that start as a forecast (never a promise)

The forecast is the first moment a unit is free for the request's length by the planned ends;
it does not place the requests ahead in the line, whose number the page shows next to it.

The participant finds the request again by a random reference in the address, which names it
and authorises nothing, and a random value in a cookie whose hash the request keeps (choice I4,
the pattern of ADR 0093). A retried intake carries the same random form key and gets the same
request (A16). Pure Python, no I/O; the use cases are ``app.domain.immediate_requests``.
"""

import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.participant_links import REF_BYTES, VALUE_BYTES, hash_value
from app.domain.pool_accounting import Commitment, earliest_start
from app.domain.pool_staffing import Intake, Supply
from app.domain.service_requests import (
    Access,
    HandlingState,
    ServiceRequest,
    data_deletion_at,
)

KEY_BYTES = 32
"""Size of the intake form's random key (a fixed crypto parameter, like ADR 0093's)."""

KEY_LENGTH = 64
"""The longest intake key accepted from a form; a longer one is refused before it is hashed."""


class IntakeOutcome(StrEnum):
    """Whether an immediate request is taken in."""

    ACCEPTED = "accepted"
    FULL = "full"
    """The pool's waiting limit is reached (``capacity_pools.max_waiting``)."""
    UNAVAILABLE = "unavailable"
    """Nobody is staffed for work without appointment, or no start fits, within the look-ahead."""


@dataclass(frozen=True)
class IntakeDecision:
    """The answer of ``admit_on_demand``.

    Attributes:
        outcome: Accepted, full or unavailable.
        expected_start: The first start that fits by the planned ends, when accepted.
    """

    outcome: IntakeOutcome
    expected_start: datetime | None = None

    @property
    def accepted(self) -> bool:
        """Whether the request may be taken in."""
        return self.outcome is IntakeOutcome.ACCEPTED


def admit_on_demand(
    supply: Supply,
    commitments: Iterable[Commitment],
    *,
    now: datetime,
    duration_minutes: int,
    buffer_minutes: int,
    waiting: int,
    max_waiting: int | None,
    lookahead_minutes: int,
) -> IntakeDecision:
    """Decide whether an immediate request may join the line of its pool now.

    Args:
        supply: The pool's staffed segments from ``now`` to the end of the look-ahead plus one
            request's length.
        commitments: Everything that holds a unit of the pool over that span.
        now: The moment of the intake (UTC).
        duration_minutes: The flow revision's length.
        buffer_minutes: The organisation's wrap-up (``treatment_buffer_minutes``).
        waiting: Immediate requests of the pool that wait or are claimed and not started.
        max_waiting: The pool's waiting limit; ``None`` is no limit.
        lookahead_minutes: ``on_demand_admission_lookahead_minutes`` of the organisation.

    Example:
        Two units, two appointments at 10:00 for 30 minutes, a request of 20 minutes at 09:55
        with a look-ahead of 120 minutes: accepted, expected at 10:30 (ADR 0122 section 2.4).

    Raises:
        DomainError: ``invalid_configuration`` for a length or wrap-up out of bounds.
    """
    if max_waiting is not None and waiting >= max_waiting:
        return IntakeDecision(IntakeOutcome.FULL)
    limit = now + timedelta(minutes=lookahead_minutes)
    found = earliest_start(
        supply,
        commitments,
        now,
        duration_minutes,
        buffer_minutes,
        limit,
        intake=Intake.ON_DEMAND,
    )
    if found is None:
        return IntakeDecision(IntakeOutcome.UNAVAILABLE)
    return IntakeDecision(IntakeOutcome.ACCEPTED, found)


class RequestStatus(StrEnum):
    """What the participant is told about immediate work; never an internal state name."""

    WAITING = "waiting"
    CALLED = "called"
    """A staff member took it and is about to start."""
    IN_PROGRESS = "in_progress"
    DONE = "done"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"


_STATUS: dict[HandlingState, RequestStatus] = {
    HandlingState.QUEUED: RequestStatus.WAITING,
    HandlingState.ASSIGNED: RequestStatus.CALLED,
    HandlingState.IN_PROGRESS: RequestStatus.IN_PROGRESS,
    HandlingState.RECOVERY_REQUIRED: RequestStatus.IN_PROGRESS,
    HandlingState.COMPLETED: RequestStatus.DONE,
    HandlingState.CANCELLED: RequestStatus.WITHDRAWN,
    HandlingState.EXPIRED: RequestStatus.EXPIRED,
}


def status_of(request: ServiceRequest) -> RequestStatus:
    """Return what the participant is told about ``request``."""
    return _STATUS[request.handling]


def place_in_line(line: Sequence[ServiceRequest], request: ServiceRequest) -> int | None:
    """Return the 1-based place of ``request`` among the waiting requests, or ``None``.

    ``line`` is the waiting immediate work of the request's pool; the order is first come,
    first served (choice I9). A request that does not wait has no place.
    """
    if request.handling is not HandlingState.QUEUED:
        return None
    waiting = sorted(
        (r for r in line if r.handling is HandlingState.QUEUED and r.id is not None),
        key=lambda r: (r.created_at, r.id or 0),
    )
    for place, found in enumerate(waiting, start=1):
        if found.id == request.id:
            return place
    return None


def page_open(request: ServiceRequest, now: datetime) -> bool:
    """Return whether the participant's page may still show ``request``.

    Until its personal data goes (``data_deletion_at``): afterwards the page answers like a
    request that does not exist.
    """
    moment = data_deletion_at(request)
    return moment is None or now < moment


def new_intake_key() -> str:
    """Return a fresh random key for one intake form (A16)."""
    return secrets.token_urlsafe(KEY_BYTES)


def valid_intake_key(key: str | None) -> bool:
    """Return whether ``key`` can be a key this installation handed out."""
    return key is not None and 32 <= len(key) <= KEY_LENGTH and key.isascii()


def new_access(intake_key: str) -> tuple[Access, str]:
    """Return the access of a new request and the cookie value; only hashes are kept."""
    value = secrets.token_urlsafe(VALUE_BYTES)
    access = Access(
        public_ref=secrets.token_urlsafe(REF_BYTES),
        access_hash=hash_value(value),
        intake_key_hash=hash_value(intake_key),
    )
    return access, value


def allows(request: ServiceRequest | None, cookie_value: str | None, now: datetime) -> bool:
    """Return whether a browser with ``cookie_value`` may see ``request`` now.

    The same answer for a request that does not exist, one of someone else and one whose page
    closed, so the answer tells nothing (A31).
    """
    if request is None or not cookie_value or request.access_hash is None:
        return False
    if not page_open(request, now):
        return False
    return secrets.compare_digest(request.access_hash, hash_value(cookie_value))
