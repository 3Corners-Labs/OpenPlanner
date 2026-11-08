"""The configuration package in tests (ADR 0089): passphrases, a source key and a clock."""

from datetime import UTC, datetime
from pathlib import Path

from app.adapters.crypto import SecretBox

PASSPHRASE = "correct horse battery staple"  # noqa: S105 - a test passphrase
"""The passphrase of a package exported by the adapter or through the machine API."""
PACKAGE_PASSPHRASE = "a passphrase of sixteen chars"  # noqa: S105 - a test passphrase
"""The passphrase typed into the portal's export and import forms."""
SOURCE_BOX = SecretBox(b"s" * 32)
"""The settings key of the installation a package is exported from."""
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
"""When the adapter tests configure and export an installation."""


def database_path(url: str) -> Path:
    """Return the file behind a SQLite database URL."""
    return Path(url.removeprefix("sqlite+aiosqlite:///"))
