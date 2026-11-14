"""The keys behind the fingerprints of the installation's webhook secrets (ADR 0124 point 8)."""

from pathlib import Path

from pydantic import SecretStr

from app.config import Settings
from app.keys import client_pseudonym_key, webhook_fingerprint_keys


def _settings(tmp_path: Path, **update: object) -> Settings:
    return Settings(
        environment="dev",
        management_token_key=SecretStr("n" * 40),
        dev_token_key_path=tmp_path / "k",
        **update,  # type: ignore[arg-type]
    )


def test_the_fingerprint_key_is_its_own_purpose_of_the_token_key(tmp_path: Path) -> None:
    (key,) = webhook_fingerprint_keys(_settings(tmp_path))

    assert len(key) == 32
    assert key != client_pseudonym_key(_settings(tmp_path))
    assert key != b"n" * 40


def test_during_a_rotation_of_the_token_key_the_previous_ones_fingerprint_key_follows(
    tmp_path: Path,
) -> None:
    before = _settings(tmp_path).model_copy(update={"management_token_key": SecretStr("o" * 40)})
    rotating = _settings(tmp_path, management_token_key_previous=SecretStr("o" * 40))

    current, previous = webhook_fingerprint_keys(rotating)

    assert current == webhook_fingerprint_keys(_settings(tmp_path))[0]
    assert previous == webhook_fingerprint_keys(before)[0]  # what the old key made then
