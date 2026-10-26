"""Worker process: runs due jobs and proves liveness with a heartbeat file.

Run with ``python -m app.jobs.worker``. Each iteration checks the database, runs due jobs
(``app.jobs.runner``) and writes the heartbeat.
"""

import asyncio
import signal
from contextlib import suppress

import httpx
import structlog

from app.adapters.clock import SystemClock
from app.adapters.db.engine import create_engine, is_reachable
from app.adapters.db.jobs import SqlJobQueue
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.integrations import build_integrations, secret_box
from app.adapters.senders import ConfiguredSenders
from app.adapters.smtp import SmtpConfig
from app.config import Settings
from app.domain.admin import AdminService
from app.domain.integration import IntegrationService
from app.domain.ports import Clock
from app.i18n import Translator, load_catalogues
from app.jobs.messages import MessageComposer
from app.jobs.runner import JobRunner
from app.keys import management_token_key
from app.logging import configure_logging

log = structlog.get_logger()


def fallback_mail(settings: Settings) -> SmtpConfig | None:
    """Return the SMTP server from the environment, or ``None`` when ``SMTP_HOST`` is not set.

    Used for organisations without their own SMTP settings (ADR 0022).
    """
    if not settings.smtp_host:
        return None
    return SmtpConfig(
        host=settings.smtp_host,
        port=settings.smtp_port,
        sender=settings.mail_from,
        starttls=settings.smtp_starttls,
        username=settings.smtp_username,
        password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
    )


async def run_worker(settings: Settings, stop: asyncio.Event, clock: Clock) -> None:
    """Run the poll loop until ``stop`` is set.

    After every iteration in which the database answered, the current time is written to
    ``settings.worker_heartbeat_path``; ``app.jobs.healthcheck`` reads it. A failing job run
    is logged and does not stop the loop.

    Args:
        settings: Database, mail and worker settings.
        stop: Set by a signal handler (or a test) to end the loop after the current iteration.
        clock: Source of the current time.
    """
    engine = create_engine(settings.database_url)
    http = httpx.AsyncClient(timeout=15.0)
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
    )
    log.info(
        "worker.started",
        poll_seconds=settings.worker_poll_seconds,
        fallback_mail=fallback is not None,
        video_enabled=integrations.video is not None,
        dispatch_enabled=integrations.dispatcher is not None,
    )
    try:
        while not stop.is_set():
            if await is_reachable(engine):
                try:
                    await runner.run_due()
                except Exception as exc:  # e.g. schema not migrated yet; keep the loop alive
                    log.warning("worker.run_failed", error=type(exc).__name__)
                settings.worker_heartbeat_path.write_text(clock.now().isoformat())
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
    finally:
        await http.aclose()
        await engine.dispose()
        log.info("worker.stopped")


async def _main() -> None:
    """Configure logging, install SIGTERM/SIGINT handlers and run the loop."""
    settings = Settings()
    configure_logging(settings.log_level)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await run_worker(settings, stop, clock=SystemClock())


if __name__ == "__main__":
    asyncio.run(_main())
