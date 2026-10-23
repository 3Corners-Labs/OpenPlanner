"""Resolve bootstrap keys from the environment (ADR 0008, ADR 0011)."""

import os
import secrets
from pathlib import Path

from app.config import Settings

MIN_KEY_BYTES = 32


def management_token_key(settings: Settings) -> bytes:
    """Return the key that derives management tokens.

    ``MANAGEMENT_TOKEN_KEY`` wins. Outside production, without it, web and worker share a
    random key that is created once in the data volume, so links survive restarts.

    Raises:
        RuntimeError: In production without the key, or if the key is too short.
    """
    if settings.management_token_key is not None:
        key = settings.management_token_key.get_secret_value().encode()
    elif settings.environment == "prod":
        raise RuntimeError("MANAGEMENT_TOKEN_KEY is required in production")
    else:
        key = _shared_dev_key(settings.dev_token_key_path)
    if len(key) < MIN_KEY_BYTES:
        raise RuntimeError(f"MANAGEMENT_TOKEN_KEY must be at least {MIN_KEY_BYTES} bytes")
    return key


def _shared_dev_key(path: Path) -> bytes:
    """Return the development key in ``path``, creating it atomically on first use."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes().strip()
    with os.fdopen(fd, "w") as handle:
        handle.write(secrets.token_urlsafe(48))
    return path.read_bytes().strip()
