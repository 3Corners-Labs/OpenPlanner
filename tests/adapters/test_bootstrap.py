"""``python -m app.cli init``: the three bootstrap keys in a private file (ADR 0042 §9)."""

import stat
from pathlib import Path

import pytest

from app.adapters.bootstrap import (
    GENERATED,
    InitRefusedError,
    remaining_variables,
    write_secrets,
)


def _values(path: Path) -> dict[str, str]:
    lines = [line for line in path.read_text().splitlines() if line and not line.startswith("#")]
    return dict(line.split("=", 1) for line in lines)


def test_writes_three_random_keys_to_a_private_file(tmp_path: Path) -> None:
    target = tmp_path / "secrets" / "secrets.env"

    write_secrets(target)

    values = _values(target)
    assert set(values) == {"SETTINGS_ENCRYPTION_KEY", "MANAGEMENT_TOKEN_KEY", "SESSION_SECRET"}
    assert all(len(value) >= 64 for value in values.values())
    assert len(set(values.values())) == 3
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


def test_two_runs_give_different_keys(tmp_path: Path) -> None:
    write_secrets(tmp_path / "a.env")
    write_secrets(tmp_path / "b.env")

    assert _values(tmp_path / "a.env") != _values(tmp_path / "b.env")


def test_an_existing_file_is_never_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "secrets.env"
    target.write_text("SETTINGS_ENCRYPTION_KEY=keep-me\n")

    with pytest.raises(InitRefusedError):
        write_secrets(target)

    assert target.read_text() == "SETTINGS_ENCRYPTION_KEY=keep-me\n"


def test_the_generated_keys_are_the_bootstrap_keys() -> None:
    assert GENERATED == ("settings_encryption_key", "management_token_key", "session_secret")


def test_remaining_variables_are_named_without_values() -> None:
    names = [spec.env for spec in remaining_variables()]

    assert "PUBLIC_BASE_URL" in names
    assert "OIDC_DISCOVERY_URL" in names
    assert "OIDC_CLIENT_SECRET" in names
    assert not {"SESSION_SECRET", "MANAGEMENT_TOKEN_KEY", "SETTINGS_ENCRYPTION_KEY"} & set(names)
    assert "DEV_TOKEN_KEY_PATH" not in names
    assert "RATE_LIMIT_BOOKINGS" not in names
