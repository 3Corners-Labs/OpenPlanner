"""``X_FILE`` for the bootstrap secrets: Docker and Kubernetes secrets (ADR 0042 §9)."""

from pathlib import Path

import pytest

from app.config import FILE_SECRETS, Settings, loose_secret_files

KEY = "k" * 48


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "dev")
    for name in FILE_SECRETS:
        monkeypatch.delenv(name.upper(), raising=False)
        monkeypatch.delenv(f"{name.upper()}_FILE", raising=False)


def _secret_file(tmp_path: Path, content: str, mode: int = 0o600) -> Path:
    path = tmp_path / "secret"
    path.write_text(content)
    path.chmod(mode)
    return path


def test_the_file_holds_the_value_without_its_trailing_newline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SESSION_SECRET_FILE", str(_secret_file(tmp_path, KEY + "\n")))

    secret = Settings().session_secret

    assert secret is not None
    assert secret.get_secret_value() == KEY


def test_every_bootstrap_secret_accepts_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in FILE_SECRETS:
        path = tmp_path / name
        path.write_text(f"{name}-value")
        path.chmod(0o600)
        monkeypatch.setenv(f"{name.upper()}_FILE", str(path))

    settings = Settings()

    for name in FILE_SECRETS:
        assert getattr(settings, name).get_secret_value() == f"{name}-value"


def test_both_the_variable_and_its_file_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MANAGEMENT_TOKEN_KEY", KEY)
    monkeypatch.setenv("MANAGEMENT_TOKEN_KEY_FILE", str(_secret_file(tmp_path, KEY)))

    with pytest.raises(ValueError, match="MANAGEMENT_TOKEN_KEY and MANAGEMENT_TOKEN_KEY_FILE"):
        Settings()


def test_a_missing_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY_FILE", str(tmp_path / "absent"))

    with pytest.raises(ValueError, match="SETTINGS_ENCRYPTION_KEY_FILE names no readable file"):
        Settings()


@pytest.mark.parametrize("content", ["", "\n"])
def test_an_empty_file_is_refused(
    content: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SESSION_SECRET_FILE", str(_secret_file(tmp_path, content)))

    with pytest.raises(ValueError, match="SESSION_SECRET_FILE is empty"):
        Settings()


def test_errors_never_show_the_value_or_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _secret_file(tmp_path, KEY)
    monkeypatch.setenv("SESSION_SECRET", KEY)
    monkeypatch.setenv("SESSION_SECRET_FILE", str(path))

    with pytest.raises(ValueError, match="both set") as caught:
        Settings()

    assert KEY not in str(caught.value)
    assert str(path) not in str(caught.value)


def test_a_file_readable_by_others_is_accepted_and_reported_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loose = _secret_file(tmp_path, KEY, mode=0o644)
    monkeypatch.setenv("SESSION_SECRET_FILE", str(loose))

    assert Settings().session_secret is not None
    assert loose_secret_files({"SESSION_SECRET_FILE": str(loose)}) == ["SESSION_SECRET_FILE"]


def test_a_private_file_is_not_reported(tmp_path: Path) -> None:
    private = _secret_file(tmp_path, KEY, mode=0o600)

    assert loose_secret_files({"session_secret_file": str(private)}) == []


def test_files_are_only_for_bootstrap_secrets() -> None:
    assert set(FILE_SECRETS) == {
        "settings_encryption_key",
        "settings_encryption_key_previous",
        "management_token_key",
        "management_token_key_previous",
        "session_secret",
        "oidc_client_secret",
    }


def test_the_previous_keys_come_from_files_and_an_empty_value_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0094: ``X_PREVIOUS`` follows ``X_FILE`` (ADR 0042 §9); empty means no rotation."""
    monkeypatch.setenv("MANAGEMENT_TOKEN_KEY_PREVIOUS_FILE", str(_secret_file(tmp_path, KEY)))
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY_PREVIOUS", "")

    settings = Settings()

    assert settings.management_token_key_previous is not None
    assert settings.management_token_key_previous.get_secret_value() == KEY
    assert settings.settings_encryption_key_previous is None
