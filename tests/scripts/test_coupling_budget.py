"""The coupling budget check: what it counts, when it fails and when it only hints (ADR 0127)."""

import json
from pathlib import Path

import pytest

from scripts import coupling_budget as cb


def _tree(tmp_path: Path) -> Path:
    """Build a tiny ``app/`` with one symbol used in two files and one function that calls three."""
    app = tmp_path / "app"
    app.mkdir()
    (app / "core.py").write_text(
        "class Thing:\n    pass\n\n\ndef wire():\n    a()\n    b.c()\n    a()\n    d(e())\n",
        encoding="utf-8",
    )
    (app / "one.py").write_text("from app.core import Thing\n", encoding="utf-8")
    (app / "two.py").write_text("x = core.Thing\n", encoding="utf-8")
    (app / "three.py").write_text("y = 1\n", encoding="utf-8")
    return tmp_path


def _entry(symbol: str, kind: str, ceiling: int, path: str = "app/core.py") -> cb.Entry:
    return cb.Entry(symbol, path, kind, ceiling, "test")


def test_afferent_counts_files_that_name_the_symbol_but_not_the_defining_one(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path)
    assert cb.afferent(root, _entry("Thing", "afferent", 9)) == 2


def test_efferent_counts_different_names_called_in_the_body(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    # a, c (the attribute of b.c), d and e; a called twice counts once.
    assert cb.efferent(root, _entry("wire", "efferent", 9)) == 4


def test_a_measure_over_its_ceiling_fails(tmp_path: Path) -> None:
    failures, hints = cb.report(_tree(tmp_path), [_entry("Thing", "afferent", 1)])
    assert len(failures) == 1
    assert "over its ceiling" in failures[0]
    assert hints == []


def test_a_measure_far_under_its_ceiling_only_hints(tmp_path: Path) -> None:
    failures, hints = cb.report(_tree(tmp_path), [_entry("Thing", "afferent", 50)])
    assert failures == []
    assert len(hints) == 1


def test_a_missing_afferent_symbol_fails_instead_of_counting_zero(tmp_path: Path) -> None:
    failures, hints = cb.report(_tree(tmp_path), [_entry("Renamed", "afferent", 5)])
    assert failures == ["Renamed: not found in app/core.py"]
    assert hints == []


def test_a_missing_defining_file_fails(tmp_path: Path) -> None:
    entry = _entry("Thing", "afferent", 5, path="app/gone.py")
    failures, _ = cb.report(_tree(tmp_path), [entry])
    assert failures == ["Thing: not found in app/gone.py"]


def test_main_reports_an_unusable_budget_file_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "budget.json"
    bad.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cb, "BUDGET_FILE", bad)
    assert cb.main(["--root", str(tmp_path)]) == 1
    assert "unusable" in capsys.readouterr().err


def test_a_missing_symbol_fails(tmp_path: Path) -> None:
    failures, _ = cb.report(_tree(tmp_path), [_entry("gone", "efferent", 5)])
    assert failures == ["gone: not found in app/core.py"]


def test_the_budget_file_of_the_repository_loads_and_holds_today(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    failures, _ = cb.report(root, cb.load())
    assert failures == []


@pytest.mark.parametrize(
    "raw",
    [
        {"symbol": "S", "path": "p", "kind": "bogus", "ceiling": 3, "reason": "r"},
        {"symbol": "S", "path": "p", "kind": "afferent", "ceiling": 0, "reason": "r"},
        {"symbol": "S", "path": "p", "kind": "afferent", "ceiling": 3, "reason": " "},
    ],
)
def test_an_entry_needs_a_known_kind_a_ceiling_and_a_reason(
    tmp_path: Path, raw: dict[str, object]
) -> None:
    file = tmp_path / "budget.json"
    file.write_text(json.dumps({"symbols": [raw]}), encoding="utf-8")
    with pytest.raises(ValueError, match="bad budget entry"):
        cb.load(file)
