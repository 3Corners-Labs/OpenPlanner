"""The worker's heartbeat file, read by its container healthcheck and the overview.

The worker writes the current time to ``WORKER_HEARTBEAT_PATH`` on every loop; the file lives
on the shared data volume, so the web process can show on the overview whether a worker is
alive (ADR 0061 §2, *System status*).
"""

from datetime import datetime
from pathlib import Path


def read_heartbeat(path: Path) -> datetime | None:
    """Return the aware timestamp in ``path``, or ``None`` when there is no usable heartbeat.

    A missing file, unparsable content or a naive timestamp counts as no heartbeat.
    """
    try:
        beat = datetime.fromisoformat(path.read_text().strip())
    except (OSError, ValueError):
        return None
    return beat if beat.tzinfo is not None else None
