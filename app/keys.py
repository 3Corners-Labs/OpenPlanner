"""Resolve bootstrap keys from the environment (ADR 0008, ADR 0011, ADR 0013)."""

import os
import secrets
from pathlib import Path

from pydantic import SecretStr

from app.config import Settings

MIN_KEY_BYTES = 32


def _bootstrap_key(settings: Settings, value: SecretStr | None, name: str, dev_path: Path) -> bytes:
    """Return a bootstrap key: from the environment, or a shared development key file.

    Raises:
        RuntimeError: In production without the key, or if the key is too short.
    """
    if value is not None:
        key = value.get_secret_value().encode()
    elif settings.environment == "prod":
        raise RuntimeError(f"{name} is required in production")
    else:
        key = _shared_dev_key(dev_path)
    if len(key) < MIN_KEY_BYTES:
        raise RuntimeError(f"{name} must be at least {MIN_KEY_BYTES} bytes")
    return key


def management_token_key(settings: Settings) -> bytes:
    """Return the key that derives management tokens (ADR 0011).

    ``MANAGEMENT_TOKEN_KEY`` wins. Outside production, without it, web and worker share a
    random key that is created once in the data volume, so links survive restarts.
    """
    return _bootstrap_key(
        settings,
        settings.management_token_key,
        "MANAGEMENT_TOKEN_KEY",
        settings.dev_token_key_path,
    )


def session_secret(settings: Settings) -> str:
    """Return the key that signs the admin session cookie (ADR 0013)."""
    return _bootstrap_key(
        settings, settings.session_secret, "SESSION_SECRET", settings.dev_session_key_path
    ).decode()


def settings_encryption_key(settings: Settings) -> bytes:
    """Return the key that encrypts integration secrets at rest (ADR 0008)."""
    return _bootstrap_key(
        settings,
        settings.settings_encryption_key,
        "SETTINGS_ENCRYPTION_KEY",
        settings.dev_settings_key_path,
    )


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
