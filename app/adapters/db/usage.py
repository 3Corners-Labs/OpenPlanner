"""SQL for writing usage facts (ADR 0117 point 1).

A mixin of ``SqlTransaction``, so the insert runs in the caller's transaction: a fact is
written together with the state change it describes, or not at all. The insert runs in a
savepoint. If it fails, the savepoint is rolled back, the state change still commits, and the
fact is dropped with a log line that names the kind and never a value: reporting must never
fail a participant's action. With reporting switched off nothing is written.
"""

from collections.abc import Callable
from dataclasses import asdict

import structlog
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import UsageEventRow
from app.adapters.db.types import plain
from app.domain.usage import StaffFact, UsageFact

log = structlog.get_logger()


class UsageQueries:
    """The usage facts' part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession
    _usage_enabled: Callable[[], bool]

    async def record_usage(self, fact: UsageFact | StaffFact) -> bool:
        """Write ``fact`` to the outbox in a savepoint; return whether it was written.

        Returns ``False`` when reporting is switched off (``reporting_enabled``) or when the
        insert failed; in the second case the failure is logged without any content and the
        surrounding transaction is unaffected.
        """
        if not self._usage_enabled():
            return False
        values = {name: plain(value) for name, value in asdict(fact).items()}
        try:
            async with self._session.begin_nested():
                self._session.add(UsageEventRow(**values))
                await self._session.flush()
        except SQLAlchemyError:
            log.warning("usage.fact_dropped", kind=fact.kind.value)
            return False
        return True
