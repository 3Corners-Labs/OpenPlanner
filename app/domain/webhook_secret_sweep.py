"""The periodic removal of previous webhook secrets whose overlap has ended (ADR 0124 point 8).

Housekeeping only. Whether a previous secret still verifies is decided by the clock at the moment
of each delivery (``app.domain.webhook_secrets``), so nothing depends on this running: a previous
secret that has not been removed yet no longer counts. The sweep keeps the sealed record from
holding a secret that is of no use any more, with the live overlap
(``webhook_secret_overlap_minutes``) as its measure. The write path of the record removes one
too, once no setting could keep it.

    worker housekeeping (hourly) ─► ``run`` ─► every organisation's connection ─► drop the ones past
"""

from collections.abc import Callable
from datetime import timedelta

from app.domain.authorization import Scope
from app.domain.ports import Clock, ContactCenterConnections, UnitOfWork


class PreviousSecretSweep:
    """Removes the previous webhook secrets whose overlap has ended.

    Args:
        uow: Opens the transaction that lists the organisations.
        clock: Current time.
        connections: The sealed connections; ``None`` without a settings key (nothing is sealed).
        overlap: The live overlap (``webhook_secret_overlap_minutes``), read at each run.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        connections: ContactCenterConnections | None,
        overlap: Callable[[], timedelta],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._connections = connections
        self._overlap = overlap

    async def run(self) -> int:
        """Remove the previous secrets that have ended; return how many were removed.

        No permission is asked and nothing is audited per record: the caller is the worker, the
        record keeps its current secret, and the rotation that made the previous one was audited.
        """
        if self._connections is None:
            return 0
        async with self._uow.read() as tx:
            organisations = await tx.list_organisations(Scope(everything=True))
        return await self._connections.drop_expired_previous(
            [o.id for o in organisations], self._clock.now(), self._overlap()
        )
