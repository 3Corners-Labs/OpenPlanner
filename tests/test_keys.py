from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.keys import management_token_key


def test_environment_key_wins(tmp_path: Path) -> None:
    settings = Settings(
        environment="dev",
        management_token_key=SecretStr("x" * 40),
        dev_token_key_path=tmp_path / "k",
    )

    assert management_token_key(settings) == b"x" * 40
    assert not (tmp_path / "k").exists()


def test_dev_key_is_created_once_and_shared(tmp_path: Path) -> None:
    settings = Settings(environment="dev", dev_token_key_path=tmp_path / "data" / "k")

    first = management_token_key(settings)
    second = management_token_key(settings)

    assert first == second
    assert len(first) >= 32
    assert (tmp_path / "data" / "k").stat().st_mode & 0o777 == 0o600


def test_production_requires_a_key(tmp_path: Path) -> None:
    settings = Settings(environment="dev", dev_token_key_path=tmp_path / "k")
    prod = settings.model_copy(update={"environment": "prod"})  # bypasses validation

    with pytest.raises(RuntimeError, match="required in production"):
        management_token_key(prod)


def test_short_keys_are_rejected() -> None:
    with pytest.raises(RuntimeError, match="at least 32"):
        management_token_key(Settings(environment="dev", management_token_key=SecretStr("short")))
