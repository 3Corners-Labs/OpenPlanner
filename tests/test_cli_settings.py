"""``python -m app.cli settings`` and ``settings-doc`` (ADR 0042 §10)."""

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
