"""Worker process: runs due jobs and proves liveness with a heartbeat file.

Run with ``python -m app.jobs.worker``. Each iteration checks the database, runs due jobs
(``app.jobs.runner``), watches the data volume and reclaims freed database pages
(``app.jobs.housekeeping``, ADR 0040), looks up the latest release when that is due (hourly,
ADR 0029), reports maintenance that has been on too long (ADR 0038) and writes the heartbeat.
The worker never pauses for maintenance: confirmed appointments and the privacy jobs go on.
It reloads the portal settings when they changed (ADR 0042 §6); the poll interval follows at
once.
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
from app.adapters.event_sink import StdoutEventSink
from app.adapters.integrations import (
    Integrations,
    build_integrations,
    secret_box,
    webex_fingerprint,
)
from app.adapters.live_settings import LiveSettings
from app.adapters.releases import update_service
from app.adapters.senders import ConfiguredSenders, fallback_mail
from app.config import Settings, environment, loose_secret_files
from app.domain.admin import AdminService
from app.domain.diagnostics import DiagnosticsService
from app.domain.event_context import set_service, trace_scope
from app.domain.integration import IntegrationService
from app.domain.maintenance_service import MaintenanceWatch
from app.domain.platform_settings import PlatformSettingsStore
from app.domain.ports import Clock
from app.domain.security_log import SecurityLog
from app.domain.system_log import SystemLog
from app.domain.update_service import UpdateService
from app.i18n import Translator, load_catalogues
from app.i18n.dates import configure_clock
from app.jobs.housekeeping import Housekeeping
from app.jobs.messages import MessageComposer
from app.jobs.runner import JobRunner
from app.keys import management_token_key
from app.logging import configure_logging, set_log_level, warn_loose_secret_files

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
    # ADR 0034: committed audit events also go to stdout for the log platform.
    uow = SqlUnitOfWork(engine, secret_box(settings), StdoutEventSink(settings.event_envelope))
    # ADR 0042 §6: portal values, reloaded once per loop when settings_version moved; the
    # services below read theirs through ``live`` each time they need them.
    live = LiveSettings(
        settings,
        environment(),
        PlatformSettingsStore(uow),
        clock,
        _apply,
    )
    security = SecurityLog(
        uow,
        clock,
        lambda: timedelta(minutes=live.settings.security_event_window_minutes),
        max_sources=lambda: live.settings.security_event_max_sources,
    )

    def build_runner(current: Settings) -> tuple[Integrations, JobRunner]:
        wired = build_integrations(current, engine, http, clock, security)
        return wired, JobRunner(
            SqlJobQueue(engine),
            uow,
            clock,
            ConfiguredSenders(uow, http, lambda: fallback_mail(live.settings)),
            MessageComposer(
                Translator(load_catalogues()),
                management_token_key(settings),
                settings.public_base_url,
            ),
            video=wired.video,
            dispatcher=wired.dispatcher,
            integration=IntegrationService(uow, clock, AdminService(uow, clock)),
            retention=lambda: live.settings.event_retention,
            clock_style=lambda organisation: live.for_organisation(organisation).time_format,
            product_footer=lambda organisation: live.for_organisation(organisation).product_footer,
        )

    integrations, runner = build_runner(settings)
    wired_with = webex_fingerprint(settings)
    diagnostics = DiagnosticsService(
        uow,
        clock,
        AdminService(uow, clock),
        lambda: timedelta(hours=live.settings.diagnostic_mode_hours),
    )
    housekeeping = Housekeeping.for_database(
        engine,
        uow,
        clock,
        lambda: storage_limits(live.settings),
        settings.database_url,
        diagnostics.brake,
    )
    updates = update_service(settings, uow, clock, http, live=lambda: live.settings)
    maintenance = MaintenanceWatch(
        uow,
        clock,
        SystemLog(uow, clock),
        alert_after=lambda: timedelta(hours=live.settings.maintenance_alert_hours),
        repeat=lambda: timedelta(hours=live.settings.maintenance_alert_repeat_hours),
    )
    log.info(
        "worker.started",
        poll_seconds=settings.worker_poll_seconds,
        fallback_mail=fallback_mail(settings) is not None,
        video_enabled=integrations.video is not None,
        dispatch_enabled=integrations.dispatcher is not None,
        release_check=settings.update_repository is not None,
    )
    try:
        while not stop.is_set():
            if await is_reachable(engine):
                if await live.refresh():  # the release repository or token may have changed
                    updates = update_service(live.settings, uow, clock, http)
                    if webex_fingerprint(live.settings) != wired_with:  # ADR 0042 step 6b
                        integrations, runner = build_runner(live.settings)
                        wired_with = webex_fingerprint(live.settings)
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
                await asyncio.wait_for(stop.wait(), timeout=live.settings.worker_poll_seconds)
    finally:
        await http.aclose()
        await engine.dispose()
        log.info("worker.stopped")


def _apply(current: Settings) -> None:
    """Follow changed portal values that live outside the services: log level, clock style."""
    set_log_level(current.log_level)
    configure_clock(current.time_format)


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
    warn_loose_secret_files(loose_secret_files(environment()))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await run_worker(settings, stop, clock=SystemClock())


if __name__ == "__main__":
    asyncio.run(_main())
