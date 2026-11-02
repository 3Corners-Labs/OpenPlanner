"""Resolve bootstrap keys from the environment and derive keys from them (ADR 0008, 0011, 0043)."""

import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import SecretStr

from app.config import Settings

MIN_KEY_BYTES = 32

CLIENT_PSEUDONYM_INFO = b"openplanner/client-pseudonym/v1"
"""HKDF label of the key behind caller pseudonyms (ADR 0053); a new label means new pseudonyms."""

AGENT_PSEUDONYM_INFO = b"openplanner/agent-pseudonym/v1"
"""HKDF label of the agent pseudonym key (ADR 0043); a new label makes new pseudonyms."""


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


def _previous_key(value: SecretStr | None, name: str) -> bytes | None:
    """Return a previous key, or ``None`` outside a rotation.

    Raises:
        RuntimeError: If the key is shorter than ``MIN_KEY_BYTES``.
    """
    if value is None:
        return None
    key = value.get_secret_value().encode()
    if len(key) < MIN_KEY_BYTES:
        raise RuntimeError(f"{name} must be at least {MIN_KEY_BYTES} bytes")
    return key


def management_token_key_previous(settings: Settings) -> bytes | None:
    """Return ``MANAGEMENT_TOKEN_KEY_PREVIOUS`` (ADR 0094), set only during a rotation."""
    return _previous_key(settings.management_token_key_previous, "MANAGEMENT_TOKEN_KEY_PREVIOUS")


def settings_encryption_key_previous(settings: Settings) -> bytes | None:
    """Return ``SETTINGS_ENCRYPTION_KEY_PREVIOUS`` (ADR 0094), set only during a rotation."""
    return _previous_key(
        settings.settings_encryption_key_previous, "SETTINGS_ENCRYPTION_KEY_PREVIOUS"
    )


def _derived(settings: Settings, info: bytes) -> bytes:
    """Return a 32-byte key derived from ``MANAGEMENT_TOKEN_KEY`` with HKDF-SHA256 under ``info``.

    Each purpose has its own label, so the token key is never used for a second purpose,
    neither key reveals the other and no extra secret has to be managed. Rotating the token
    key changes every derived key.
    """
    return _derive_from(management_token_key(settings), info)


def _derive_from(key: bytes, info: bytes) -> bytes:
    """Return the 32-byte HKDF-SHA256 key of ``key`` under the label ``info``."""
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(key)


def client_pseudonym_key(settings: Settings) -> bytes:
    """Return the key that turns caller addresses into pseudonyms (ADR 0053)."""
    return _derived(settings, CLIENT_PSEUDONYM_INFO)


def agent_pseudonym_key(settings: Settings) -> bytes:
    """Return the key of the agents' pseudonyms in the own queue (ADR 0043)."""
    return _derived(settings, AGENT_PSEUDONYM_INFO)


def agent_pseudonym_key_previous(settings: Settings) -> bytes | None:
    """Return the agents' pseudonym key under ``MANAGEMENT_TOKEN_KEY_PREVIOUS``, if one is set.

    Claims made before a rotation are recognised as the agent's own while it is set.
    """
    previous = management_token_key_previous(settings)
    return None if previous is None else _derive_from(previous, AGENT_PSEUDONYM_INFO)


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
