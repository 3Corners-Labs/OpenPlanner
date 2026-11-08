"""Which release fixtures the upgrade matrix runs on (ADR 0116 point 7)."""

import io
import json
from typing import Any

import pytest

from scripts.upgrade_window import RELEASE_UPLOADER, choose, fixture_name, main


def release(tag: str, *, fixture: bool = True, by: str = RELEASE_UPLOADER) -> dict[str, Any]:
    name = fixture_name(tag)
    assets = [{"name": n, "uploader": {"login": by}} for n in (name, f"{name}.sha256")]
    return {"tag_name": tag, "draft": False, "assets": assets if fixture else []}


def test_the_newest_patch_of_the_newest_two_minors() -> None:
    releases = [
        release("v0.7.1"),
        release("v0.7.0"),
        release("v0.6.3"),
        release("v0.6.2"),
        release("v0.5.0"),
        release("v0.8.0-rc.4"),
    ]

    assert choose(releases) == ["v0.7.1", "v0.6.3"]


def test_releases_without_a_fixture_from_the_release_job_are_skipped() -> None:
    releases = [
        release("v0.7.1", fixture=False),
        release("v0.7.0", by="someone"),
        release("v0.6.0"),
        {**release("v0.8.0"), "draft": True},
    ]

    assert choose(releases) == ["v0.6.0"]


def test_the_stop_joins_the_window() -> None:
    releases = [release("v1.3.0"), release("v1.2.0"), release("v1.0.4"), release("v1.0.0")]

    assert choose(releases, stop="1.0.0") == ["v1.3.0", "v1.2.0", "v1.0.4"]
    assert choose(releases, stop="1.2.0") == ["v1.3.0", "v1.2.0"]
    assert choose(releases, stop="0.9.0") == ["v1.3.0", "v1.2.0"]


def test_no_fixture_yet_prints_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps([release("v0.5.1", fixture=False)])))

    assert main([]) == 0
    assert capsys.readouterr().out == ""


def test_main_prints_one_tag_per_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps([release("v0.6.0"), release("v0.5.0")]))
    )

    assert main(["--stop", "0.5.0"]) == 0
    assert capsys.readouterr().out == "v0.6.0\nv0.5.0\n"
