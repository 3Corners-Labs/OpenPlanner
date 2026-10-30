"""The certificate of the public address: probed on schedule, stored and reported (ADR 0064).

    TlsWatch.check_if_due ─► CertificateProbe.probe ─► judge ─► CertificateCheck (stored)
                                                            ─► changed, or lasting? ─► system event

A finding other than ``ok`` becomes a system event ``tls.certificate`` the first time it is
seen and again every repeat period while it lasts; the return to ``ok`` is recorded once. The
events carry codes only: the finding, the issuer's name and the days left, never anything about
a participant. ``CertificateStatus`` reads the stored check for *System status* and the CLI.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from app.domain.audit import Outcome
from app.domain.ports import CertificateProbe, Clock, UnitOfWork
from app.domain.system_log import SystemLog
from app.domain.tls import EVENT, Address, CertificateCheck, Finding, judge, public_address
from app.domain.tunable import value_of

ISSUER_IN_EVENT = 100
"""Longest issuer name in the event's detail: the server chooses it, the column is 255 wide."""


class CertificateStatus:
    """Reads the latest check for System status and the CLI.

    Args:
        uow: Reads the stored check.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        """Store the unit of work."""
        self._uow = uow

    async def latest(self) -> CertificateCheck | None:
        """Return the latest check, if the worker or the CLI ran one."""
        async with self._uow.read() as tx:
            return await tx.get_certificate_check()


class TlsWatch:
    """Probes the public address on schedule, stores the outcome and reports changes (worker).

    Args:
        uow: Stores the check.
        clock: Current time.
        probe: Performs the TLS handshake.
        system_log: Where the events go (global: only the superuser sees them).
        public_base_url: The address participants use; an ``http`` one has nothing to check.
        interval: How often the certificate is probed.
        warn_within: Days left below which the finding is ``expiring``.
        repeat: How often a lasting finding is reported again.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        probe: CertificateProbe,
        system_log: SystemLog,
        *,
        public_base_url: str,
        interval: timedelta | Callable[[], timedelta],
        warn_within: timedelta | Callable[[], timedelta],
        repeat: timedelta | Callable[[], timedelta],
    ) -> None:
        """Store the collaborators; nothing has been probed or reported yet."""
        self._uow = uow
        self._clock = clock
        self._probe = probe
        self._system_log = system_log
        self._address = public_address(public_base_url)
        self._host = self._address.host if self._address else urlsplit(public_base_url).hostname
        self._interval = interval
        self._warn_within = warn_within
        self._repeat = repeat
        self._reported: tuple[Finding, datetime] | None = None

    async def check_if_due(self) -> CertificateCheck | None:
        """Probe when the latest stored check is older than the interval; return the new one."""
        now = self._clock.now()
        async with self._uow.read() as tx:
            previous = await tx.get_certificate_check()
        if previous is not None and now - previous.checked_at < value_of(self._interval):
            return None
        return await self.check(previous)

    async def check(self, previous: CertificateCheck | None = None) -> CertificateCheck:
        """Probe now, store the outcome and report it when it changed or keeps failing.

        Args:
            previous: The stored check, when the caller already read it.
        """
        now = self._clock.now()
        check = await self.probe(address=None)
        async with self._uow.locked() as tx:
            if previous is None:
                previous = await tx.get_certificate_check()
            await tx.save_certificate_check(check)
        await self._report(check, previous, now)
        return check

    async def probe(self, address: Address | None) -> CertificateCheck:
        """Probe ``address`` (default: the public address) and judge the result, storing nothing."""
        now = self._clock.now()
        target = address or self._address
        if target is None:
            return CertificateCheck(now, self._host or "", Finding.NOT_HTTPS)
        result = await self._probe.probe(target.host, target.port)
        finding = judge(result, target.host, now, value_of(self._warn_within))
        return CertificateCheck(
            now, target.host, finding, result.certificate, result.trust, result.error
        )

    async def _report(
        self, check: CertificateCheck, previous: CertificateCheck | None, now: datetime
    ) -> None:
        """Record a system event on a change, and repeat a lasting problem."""
        changed = previous is None or previous.finding is not check.finding
        if check.ok:
            if changed and previous is not None:
                await self._record(check, Outcome.SUCCESS, now)
            self._reported = None
            return
        if not changed and self._reported is not None:
            finding, at = self._reported
            if finding is check.finding and now - at < value_of(self._repeat):
                return
        await self._record(check, Outcome.FAILURE, now)
        self._reported = (check.finding, now)

    async def _record(self, check: CertificateCheck, outcome: Outcome, now: datetime) -> None:
        """Write the system event: finding, issuer and days left, nothing else."""
        details = []
        if check.certificate is not None:
            details.append(f"days:{check.certificate.days_left(now)}")
            details.append(f"issuer:{check.certificate.issuer[:ISSUER_IN_EVENT]}")
        if check.error:
            details.append(f"error:{check.error}")
        await self._system_log.record(
            EVENT, outcome, reason=check.finding.value, detail=",".join(details)
        )
