"""Worker process: polls for due jobs and proves liveness with a heartbeat file.

Run with ``python -m app.jobs.worker``. Job types and the ``jobs`` table arrive in work
package 4; until then each iteration only checks the database.
"""

import asyncio
import signal
from contextlib import suppress

import structlog

from app.adapters.clock import SystemClock
from app.adapters.db.engine import create_engine, is_reachable
from app.config import Settings
from app.domain.ports import Clock
from app.logging import configure_logging

log = structlog.get_logger()


async def run_worker(settings: Settings, stop: asyncio.Event, clock: Clock) -> None:
    """Run the poll loop until ``stop`` is set.

    After every iteration in which the database answered, the current time is written to
    ``settings.worker_heartbeat_path``; ``app.jobs.healthcheck`` reads it.

    Args:
        settings: Database URL, poll interval and heartbeat path.
        stop: Set by a signal handler (or a test) to end the loop after the current iteration.
        clock: Source of the heartbeat timestamp.
    """
    engine = create_engine(settings.database_url)
    log.info("worker.started", poll_seconds=settings.worker_poll_seconds)
    try:
        while not stop.is_set():
            if await is_reachable(engine):
                settings.worker_heartbeat_path.write_text(clock.now().isoformat())
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
    finally:
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
