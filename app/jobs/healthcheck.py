"""Worker container healthcheck: exit 0 when the heartbeat is fresh, 1 otherwise.

Run with ``python -m app.jobs.healthcheck`` (see ``docker-compose.yml``).
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

from app.adapters.clock import SystemClock
from app.adapters.heartbeat import read_heartbeat
from app.config import Settings


def heartbeat_is_fresh(path: Path, max_age: timedelta, now: datetime) -> bool:
    """Return whether ``path`` holds an aware ISO timestamp no older than ``max_age``.

    A missing file, unparsable content or a naive timestamp counts as not fresh.
    """
    beat = read_heartbeat(path)
    return beat is not None and now - beat <= max_age


def main() -> int:
    """Check the heartbeat configured in the environment and return the exit code."""
    settings = Settings()
    fresh = heartbeat_is_fresh(
        settings.worker_heartbeat_path,
        timedelta(seconds=settings.worker_heartbeat_max_age_seconds),
        SystemClock().now(),
    )
    return 0 if fresh else 1


if __name__ == "__main__":
    sys.exit(main())
