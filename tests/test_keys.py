from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.keys import client_pseudonym_key, management_token_key, session_secret


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


def test_session_secret_follows_the_same_rules(tmp_path: Path) -> None:
    dev = Settings(environment="dev", dev_session_key_path=tmp_path / "session.key")

    assert session_secret(dev) == session_secret(dev)
    assert len(session_secret(dev)) >= 32
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        session_secret(dev.model_copy(update={"environment": "prod"}))


def test_the_pseudonym_key_is_derived_and_differs_from_the_token_key(tmp_path: Path) -> None:
    def settings(value: str) -> Settings:
        return Settings(
            environment="dev",
            management_token_key=SecretStr(value),
            dev_token_key_path=tmp_path / "k",
        )

    key = client_pseudonym_key(settings("x" * 40))

    assert len(key) == 32
    assert key == client_pseudonym_key(settings("x" * 40))
    assert key != b"x" * 32
    assert key != client_pseudonym_key(settings("y" * 40))
