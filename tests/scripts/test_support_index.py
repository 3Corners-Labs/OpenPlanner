"""support.json: the support status of every minor's line (ADR 0116 points 2 and 9)."""

import json
from datetime import date
from pathlib import Path

import pytest

from app.domain.update_policy import parse_support
from app.domain.updates import LineStatus
from scripts import support_index
from scripts.support_index import index, write
from tests.scripts.conftest import Git, commit_all

TODAY = date(2026, 11, 8)


def statuses(data: dict[str, object]) -> list[tuple[str, str]]:
    return [(e["line"], e["status"]) for e in data["lines"]]  # type: ignore[index, union-attr]


def test_before_one_only_the_newest_minor_is_supported() -> None:
    data = index(["v0.5.1", "v0.5.0", "v0.4.0", "v0.6.0-rc.2"], None, TODAY)

    assert statuses(data) == [("0.5", "supported"), ("0.4", "ended")]


def test_from_one_the_minor_before_gets_security_fixes() -> None:
    data = index(["1.2.0", "1.1.3", "1.0.0", "0.9.0"], None, TODAY)

    assert statuses(data) == [
        ("1.2", "supported"),
        ("1.1", "security"),
        ("1.0", "ended"),
        ("0.9", "ended"),
    ]


def test_an_extended_line_is_kept_until_its_date() -> None:
    existing = {
        "lines": [
            {"line": "1.0", "status": "extended", "until": "2027-06-30"},
            {"line": "0.9", "status": "extended", "until": "2026-01-01"},
        ]
    }

    data = index(["1.2.0", "1.1.0", "1.0.0", "0.9.0"], existing, TODAY)

    assert statuses(data)[2:] == [("1.0", "extended"), ("0.9", "ended")]
    assert data["lines"][2]["until"] == "2027-06-30"  # type: ignore[index]


def test_the_file_is_what_the_portal_reads(tmp_path: Path) -> None:
    path = tmp_path / "support.json"

    write(path, ["v1.1.0", "v1.0.2"], TODAY)

    lines = parse_support(path.read_text())
    assert [(line.line, line.status) for line in lines] == [
        ("1.1", LineStatus.SUPPORTED),
        ("1.0", LineStatus.SECURITY),
    ]


def test_main_reads_the_tags_and_adds_the_release(
    git: Git, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.txt").write_text("a\n")
    commit_all(git, "chore(release): v0.5.0 (#1)")
    git("tag", "-a", "v0.5.0", "-m", "v0.5.0")
    git("tag", "-a", "v0.6.0-rc.1", "-m", "candidate")

    assert support_index.main(["--add", "0.6.0", "--today", "2026-11-08"]) == 0

    data = json.loads((tmp_path / "support.json").read_text())
    assert statuses(data) == [("0.6", "supported"), ("0.5", "ended")]
    assert "written" in capsys.readouterr().out


def test_the_repositorys_support_json_is_current() -> None:
    root = Path(__file__).resolve().parents[2]
    lines = parse_support((root / "support.json").read_text())

    assert lines[0].status is LineStatus.SUPPORTED
