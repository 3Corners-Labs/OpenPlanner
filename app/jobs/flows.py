"""The worker's one repair of the legacy pools and flows after an upgrade (ADR 0121 step 2e)."""

import structlog

from app.domain.default_flows_service import ensure_all_default_flows
from app.domain.ports import UnitOfWork

log = structlog.get_logger()


async def repair_default_flows(uow: UnitOfWork) -> None:
    """Give a group that an older image created its pool and flows; log counts, never raise.

    Run once when the worker starts: it is cheap and idempotent (without a missing row it takes
    no write lock), and a failure (for example the schema not migrated yet) is logged with the
    error type and left for the next start.
    """
    try:
        counts = await ensure_all_default_flows(uow)
    except Exception as exc:  # never keep the worker from starting
        log.warning("flows.repair_failed", error=type(exc).__name__)
        return
    if counts.repaired:
        log.info("flows.repaired", groups=counts.repaired)
    if counts.failed:
        log.warning("flows.repair_incomplete", groups=counts.failed)
