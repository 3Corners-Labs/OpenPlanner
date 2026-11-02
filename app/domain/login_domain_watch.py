"""The periodic re-check of verified login domains (ADR 0083 point 12, step 3b).

Every ``idp_domain_recheck_hours`` the worker looks up the TXT record of each domain that
routes (verified or failing). A record that disappears makes the domain *failing*: it keeps
routing for ``idp_domain_grace_days``, then *lapses* and stops. A failed lookup changes nothing
(``login_domains.checked``), so a resolver outage never lapses a domain.

    run ─► claims that route and are due ─► TXT lookup ─► checked ─► stored
                                                                 └─ state changed? ─► system event

A change of state becomes a system event ``idp.domain_check`` with the provider key, the domain
and the new state; the superuser sees it in the system log and the alerts. No personal data.
"""

from collections.abc import Callable
from datetime import datetime, timedelta

from app.domain.audit import Outcome
from app.domain.authorization import Scope
from app.domain.login_domains import DomainClaim, DomainState, checked
from app.domain.ports import Clock, MailDomainResolver, UnitOfWork
from app.domain.system_log import SystemLog

EVENT = "idp.domain_check"
"""The system event of a domain whose state changed at a re-check."""


class DomainWatch:
    """Re-checks the routing login domains when they are due.

    Args:
        uow: Reads and stores the claims.
        clock: Current time.
        resolver: Looks up TXT records; ``None`` answers nothing, which changes nothing.
        system_log: Writes the event of a changed state.
        interval: Returns ``idp_domain_recheck_hours`` as a period (read at every run).
        grace: Returns ``idp_domain_grace_days`` as a period.
    """

    def __init__(  # noqa: PLR0913
        self,
        uow: UnitOfWork,
        clock: Clock,
        resolver: MailDomainResolver | None,
        system_log: SystemLog,
        interval: Callable[[], timedelta],
        grace: Callable[[], timedelta],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._resolver = resolver
        self._system_log = system_log
        self._interval = interval
        self._grace = grace

    async def run(self) -> list[DomainClaim]:
        """Re-check every routing domain that is due; return the ones whose state changed."""
        now = self._clock.now()
        async with self._uow.read() as tx:
            claims = await tx.list_login_domains(Scope(everything=True))
        changed = []
        for claim in claims:
            if not _due(claim, now, self._interval()):
                continue
            found = None
            if self._resolver is not None:
                found = await self._resolver.txt_records(claim.record_name)
            after = checked(claim, found, self._clock.now(), self._grace())
            async with self._uow.locked() as tx:
                await tx.save_login_domain(after)
            if after.state is not claim.state:
                changed.append(after)
                await self._report(after)
        return changed

    async def _report(self, claim: DomainClaim) -> None:
        """Write the event of a changed state: provider, domain and state, nothing else."""
        outcome = Outcome.SUCCESS if claim.state is DomainState.VERIFIED else Outcome.FAILURE
        await self._system_log.record(
            EVENT,
            outcome,
            reason=claim.state.value,
            detail=f"key:{claim.provider},domain:{claim.domain}",
        )


def _due(claim: DomainClaim, now: datetime, interval: timedelta) -> bool:
    """Return whether a routing claim's last look-up is older than ``interval``."""
    if not claim.state.routes:
        return False
    return claim.checked_at is None or now - claim.checked_at >= interval
