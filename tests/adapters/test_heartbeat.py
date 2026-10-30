"""Reading the worker's heartbeat file (the web process shows it on the overview)."""

from datetime import UTC, datetime
from pathlib import Path

from app.adapters.heartbeat import read_heartbeat

BEAT = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)


def test_an_aware_timestamp_is_read(tmp_path: Path) -> None:
    path = tmp_path / "worker.heartbeat"
    path.write_text(BEAT.isoformat() + "\n")

    assert read_heartbeat(path) == BEAT


def test_a_missing_naive_or_broken_file_is_no_heartbeat(tmp_path: Path) -> None:
    naive = tmp_path / "naive"
    naive.write_text("2026-10-30T09:00:00")
    broken = tmp_path / "broken"
    broken.write_text("yesterday")

    assert read_heartbeat(tmp_path / "missing") is None
    assert read_heartbeat(naive) is None
    assert read_heartbeat(broken) is None
