"""The round trip to the public address: probed on schedule, stored and reported (ADR 0086).

    AddressWatch.check_if_due ─► store nonce ─► InstanceProbe.probe ─► judge ─► AddressCheck
                                                                          (stored per address)
                                                                      ─► changed, or lasting?
                                                                         ─► system event

The worker requests ``GET {address}/api/v1/instance?probe={nonce}`` through the public address,
as a participant's browser would, and when ``WEBHOOK_BASE_URL`` names another address, through
that one too. A finding other than ``ok`` becomes a system event ``public_address`` the first
time it is seen and again every repeat period while it lasts; the return to ``ok`` is recorded
once. The events carry codes only: the finding, the address kind and the network reason, never
anything about a participant. ``AddressStatus`` reads the stored checks for System status and
the CLI.
"""

import secrets
from collections.abc import Callable
from datetime import datetime, timedelta

from app.domain.audit import Outcome
from app.domain.ports import Clock, InstanceProbe, UnitOfWork
from app.domain.public_address import (
    EVENT,
    NONCE_BYTES,
    NONCE_LIFETIME_SECONDS,
    AddressCheck,
    AddressFinding,
    AddressKind,
    Forwarded,
    InstanceAnswer,
    InstanceReply,
    judge,
    nonce_hash,
    origin_of,
)
from app.domain.system_log import SystemLog
from app.domain.tunable import value_of


class AddressStatus:
    """Reads the latest round trips for System status and the CLI.

    Args:
        uow: Reads the stored checks.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        """Store the unit of work."""
        self._uow = uow

    async def latest(self) -> dict[AddressKind, AddressCheck]:
        """Return the latest check per address the worker or the CLI ran."""
        async with self._uow.read() as tx:
            return {check.kind: check for check in await tx.get_address_checks()}


class AddressWatch:
    """Probes the public address on schedule, stores the outcome and reports changes (worker).

    Args:
        uow: Stores the checks and the pending nonce.
        clock: Current time.
        probe: Requests the instance endpoint.
        system_log: Where the events go (global: only the superuser sees them).
        public_base_url: The address participants use.
        webhook_base_url: The address the platforms call back on, when it is another one.
        interval: How often the round trip runs (``tls_check_interval_hours``).
        repeat: How often a lasting finding is reported again.
        enabled: Whether the probe runs at all (``PUBLIC_ADDRESS_CHECK_ENABLED``).
        make_nonce: Returns a fresh nonce; random by default, fixed in tests.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        probe: InstanceProbe,
        system_log: SystemLog,
        *,
        public_base_url: str,
        webhook_base_url: str | None,
        interval: timedelta | Callable[[], timedelta],
        repeat: timedelta | Callable[[], timedelta],
        enabled: bool | Callable[[], bool] = True,
        make_nonce: Callable[[], str] = lambda: secrets.token_urlsafe(NONCE_BYTES),
    ) -> None:
        """Store the collaborators; nothing has been probed or reported yet."""
        self._uow = uow
        self._clock = clock
        self._probe = probe
        self._system_log = system_log
        self._targets = _targets(public_base_url, webhook_base_url)
        self._interval = interval
        self._repeat = repeat
        self._enabled = enabled
        self._make_nonce = make_nonce
        self._reported: dict[AddressKind, tuple[AddressFinding, datetime]] = {}

    @property
    def enabled(self) -> bool:
        """Whether the probe is switched on (``PUBLIC_ADDRESS_CHECK_ENABLED``)."""
        return value_of(self._enabled)

    async def check_if_due(self) -> list[AddressCheck]:
        """Probe when a stored check is missing or older than the interval; return the new ones."""
        if not self.enabled:
            return []
        now = self._clock.now()
        async with self._uow.read() as tx:
            stored = {check.kind: check for check in await tx.get_address_checks()}
        due = any(
            kind not in stored or now - stored[kind].checked_at >= value_of(self._interval)
            for kind in self._targets
        )
        return await self.check(stored) if due else []

    async def check(
        self, previous: dict[AddressKind, AddressCheck] | None = None
    ) -> list[AddressCheck]:
        """Probe every address now, store each outcome and report what changed or keeps failing.

        Args:
            previous: The stored checks, when the caller already read them.
        """
        async with self._uow.locked() as tx:
            if previous is None:
                previous = {check.kind: check for check in await tx.get_address_checks()}
            instance_id = await tx.ensure_instance_id()
        results = []
        for kind, url in self._targets.items():
            check = await self._round_trip(kind, url, instance_id)
            async with self._uow.locked() as tx:
                await tx.save_address_check(check)
            await self._report(check, previous.get(kind), self._clock.now())
            results.append(check)
        async with self._uow.locked() as tx:
            for kind in AddressKind:
                if kind not in self._targets and kind in previous:
                    await tx.delete_address_check(kind)
        return results

    async def _round_trip(self, kind: AddressKind, url: str, instance_id: str) -> AddressCheck:
        """Store a fresh nonce, request the endpoint with it and judge the answer."""
        nonce = self._make_nonce()
        now = self._clock.now()
        async with self._uow.locked() as tx:
            await tx.store_probe_nonce(nonce_hash(nonce), now)
        reply = await self._probe.probe(origin_of(url), nonce)
        finding = judge(reply, instance_id=instance_id, public_https=url.startswith("https://"))
        return _check(kind, now, origin_of(url), finding, reply)

    async def _report(
        self, check: AddressCheck, previous: AddressCheck | None, now: datetime
    ) -> None:
        """Record a system event on a change, and repeat a lasting problem."""
        changed = previous is None or previous.finding is not check.finding
        if check.ok:
            if changed and previous is not None:
                await self._record(check, Outcome.SUCCESS)
            self._reported.pop(check.kind, None)
            return
        known = self._reported.get(check.kind)
        if not changed and known is not None:
            finding, at = known
            if finding is check.finding and now - at < value_of(self._repeat):
                return
        await self._record(check, Outcome.FAILURE)
        self._reported[check.kind] = (check.finding, now)

    async def _record(self, check: AddressCheck, outcome: Outcome) -> None:
        """Write the system event: finding, address kind and network reason, nothing else."""
        details = [f"address:{check.kind.value}"]
        if check.error:
            details.append(f"error:{check.error}")
        await self._system_log.record(
            EVENT, outcome, reason=check.finding.value, detail=",".join(details)
        )


def _targets(public_base_url: str, webhook_base_url: str | None) -> dict[AddressKind, str]:
    """Return the addresses to probe: the public one, and the webhook one when it differs."""
    targets = {AddressKind.PUBLIC: public_base_url}
    if webhook_base_url and origin_of(webhook_base_url) != origin_of(public_base_url):
        targets[AddressKind.WEBHOOK] = webhook_base_url
    return targets


def _check(
    kind: AddressKind, now: datetime, url: str, finding: AddressFinding, reply: InstanceReply
) -> AddressCheck:
    """Build the stored outcome; the network reason is kept only where it explains a finding."""
    answer = reply.answer
    return AddressCheck(
        kind,
        now,
        url,
        finding,
        answer.scheme if answer else None,
        answer.forwarded if answer else None,
        reply.error if finding is AddressFinding.UNREACHABLE else "",
    )


class InstanceService:
    """Answers ``/api/v1/instance``: the id to every caller, the detail to the holder of the nonce.

    Args:
        uow: Reads the id and consumes the pending nonce.
        clock: Current time.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock

    async def answer(
        self, probe: str | None, *, scheme: str, forwarded: Forwarded
    ) -> InstanceAnswer:
        """Return the answer for one request.

        Args:
            probe: The nonce the caller offered, if any.
            scheme: The scheme the app saw (``http`` or ``https``).
            forwarded: What the app did with the request's forwarded headers.
        """
        if not probe:
            # The id exists from the first start (``prepare``), so a stranger's read writes nothing.
            async with self._uow.read() as tx:
                known = await tx.get_instance_id()
            if known is not None:
                return InstanceAnswer(known)
            async with self._uow.locked() as tx:
                return InstanceAnswer(await tx.ensure_instance_id())
        not_before = self._clock.now() - timedelta(seconds=NONCE_LIFETIME_SECONDS)
        async with self._uow.locked() as tx:
            instance_id = await tx.ensure_instance_id()
            accepted = await tx.take_probe_nonce(nonce_hash(probe), not_before)
        if not accepted:
            return InstanceAnswer(instance_id)
        return InstanceAnswer(instance_id, scheme, forwarded)

    async def prepare(self) -> None:
        """Make the installation's id when there is none (first start)."""
        async with self._uow.locked() as tx:
            await tx.ensure_instance_id()
