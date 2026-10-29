"""``python -m app.cli settings`` and ``settings-doc`` (ADR 0042 §10)."""

from pathlib import Path

import pytest

from app.cli import main


@pytest.fixture(autouse=True)
def _dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "dev")


def test_settings_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["settings", "list"]) == 0
    out = capsys.readouterr().out

    assert out.startswith("VARIABLE")
    assert "RATE_LIMIT_BOOKINGS" in out


def test_settings_show_accepts_any_case(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["settings", "show", "rate_limit_reads"]) == 0

    assert "variable:      RATE_LIMIT_READS" in capsys.readouterr().out


def test_settings_show_unknown_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["settings", "show", "NO_SUCH_SETTING"]) == 2

    assert "Unknown setting" in capsys.readouterr().out


def test_settings_doc(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["settings-doc"]) == 0

    assert capsys.readouterr().out.startswith("# Settings")


def test_init_writes_the_keys_and_never_prints_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Before the environment is complete: production without any key must still work.
    monkeypatch.setenv("ENVIRONMENT", "prod")
    target = tmp_path / "secrets" / "secrets.env"

    assert main(["init", "--out", str(target)]) == 0
    out = capsys.readouterr().out

    keys = [line.split("=", 1)[1] for line in target.read_text().splitlines() if "=" in line]
    assert len(keys) == 3
    assert not any(key in out for key in keys)
    assert "PUBLIC_BASE_URL" in out


def test_init_refuses_an_existing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "secrets.env"
    target.write_text("# keep\n")

    assert main(["init", "--out", str(target)]) == 1
    assert "nothing written" in capsys.readouterr().out
    assert target.read_text() == "# keep\n"
