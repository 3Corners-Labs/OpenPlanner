"""Worker process: runs due jobs and proves liveness with a heartbeat file.

Run with ``python -m app.jobs.worker``. Each iteration checks the database, runs due jobs
(``app.jobs.runner``), watches the data volume and reclaims freed database pages
(``app.jobs.housekeeping``, ADR 0040), looks up the latest release when that is due (hourly,
ADR 0029), reports maintenance that has been on too long (ADR 0038) and writes the heartbeat.
The worker never pauses for maintenance: confirmed appointments and the privacy jobs go on.
"""

import asyncio
import signal
from contextlib import suppress
from datetime import timedelta

import httpx
import structlog

from app.adapters.clock import SystemClock
from app.adapters.db.engine import create_engine, is_reachable
from app.adapters.db.jobs import SqlJobQueue
from app.adapters.db.maintenance import storage_limits
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.integrations import build_integrations, secret_box
from app.adapters.releases import update_service
from app.adapters.senders import ConfiguredSenders, fallback_mail
from app.config import Settings
from app.domain.admin import AdminService
from app.domain.event_context import set_service, trace_scope
from app.domain.integration import IntegrationService
from app.domain.maintenance_service import MaintenanceWatch
from app.domain.ports import Clock
from app.domain.system_log import SystemLog
from app.domain.update_service import UpdateService
from app.i18n import Translator, load_catalogues
from app.i18n.dates import configure_clock
from app.jobs.housekeeping import Housekeeping
from app.jobs.messages import MessageComposer
from app.jobs.runner import JobRunner
from app.keys import management_token_key
from app.logging import configure_logging

log = structlog.get_logger()


async def run_worker(
    settings: Settings,
    stop: asyncio.Event,
    clock: Clock,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Run the poll loop until ``stop`` is set.

    After every iteration in which the database answered, the current time is written to
    ``settings.worker_heartbeat_path``; ``app.jobs.healthcheck`` reads it. A failing job run
    is logged and does not stop the loop.

    Args:
        settings: Database, mail and worker settings.
        stop: Set by a signal handler (or a test) to end the loop after the current iteration.
        clock: Source of the current time.
        transport: HTTP transport for outgoing calls (tests); defaults to the network.
    """
    configure_clock(settings.time_format)
    engine = create_engine(settings.database_url)
    http = httpx.AsyncClient(timeout=15.0, transport=transport)
    integrations = build_integrations(settings, engine, http, clock)
    uow = SqlUnitOfWork(engine, secret_box(settings))
    fallback = fallback_mail(settings)
    runner = JobRunner(
        SqlJobQueue(engine),
        uow,
        clock,
        ConfiguredSenders(uow, http, fallback),
        MessageComposer(
            Translator(load_catalogues()),
            management_token_key(settings),
            settings.public_base_url,
        ),
        video=integrations.video,
        dispatcher=integrations.dispatcher,
        integration=IntegrationService(uow, clock, AdminService(uow, clock)),
        retention=timedelta(days=settings.system_event_retention_days),
    )
    housekeeping = Housekeeping.for_database(
        engine, uow, clock, storage_limits(settings), settings.database_url
    )
    updates = update_service(settings, uow, clock, http)
    maintenance = MaintenanceWatch(
        uow,
        clock,
        SystemLog(uow, clock),
        alert_after=timedelta(hours=settings.maintenance_alert_hours),
        repeat=timedelta(hours=settings.maintenance_alert_repeat_hours),
    )
    log.info(
        "worker.started",
        poll_seconds=settings.worker_poll_seconds,
        fallback_mail=fallback is not None,
        video_enabled=integrations.video is not None,
        dispatch_enabled=integrations.dispatcher is not None,
        release_check=settings.update_repository is not None,
    )
    try:
        while not stop.is_set():
            if await is_reachable(engine):
                # One trace per round for housekeeping; every job gets its own inside it.
                with trace_scope():
                    try:
                        await runner.run_due()
                    except Exception as exc:  # e.g. schema not migrated yet; keep the loop alive
                        log.warning("worker.run_failed", error=type(exc).__name__)
                    await housekeeping.run()
                    await _check_releases(updates)
                    await _watch_maintenance(maintenance)
                settings.worker_heartbeat_path.write_text(clock.now().isoformat())
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
    finally:
        await http.aclose()
        await engine.dispose()
        log.info("worker.stopped")


async def _check_releases(updates: UpdateService) -> None:
    """Look up the latest release when due; a failure is logged and never stops the loop."""
    try:
        if await updates.check_if_due():
            log.info("updates.checked", current=str(updates.current or "none"))
    except Exception as exc:  # e.g. schema not migrated yet
        log.warning("updates.check_failed", error=type(exc).__name__)


async def _watch_maintenance(watch: MaintenanceWatch) -> None:
    """Report long maintenance; a failure is logged and never stops the loop."""
    try:
        hours = await watch.check()
    except Exception as exc:  # e.g. schema not migrated yet
        log.warning("maintenance.watch_failed", error=type(exc).__name__)
        return
    if hours is not None:
        log.warning("maintenance.overdue", hours=hours)


async def _main() -> None:
    """Configure logging, install SIGTERM/SIGINT handlers and run the loop."""
    settings = Settings()
    set_service("worker")
    configure_logging(settings.log_level, envelope=settings.event_envelope)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await run_worker(settings, stop, clock=SystemClock())


if __name__ == "__main__":
    asyncio.run(_main())
