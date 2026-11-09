"""The release a capture shows and the version frozen per release (role-manuals' release.py).

The title pages say which software the manuals show: the label comes from the release tag, never
from ``pyproject.toml``, which names the previous release until the next one is cut. A frozen
version is whole, read-only and checkable, or it is not there at all.
"""

import datetime as dt
import hashlib
import json
import os
import time
import zipfile
from pathlib import Path

import pytest

from tests.support.git_repository import Git, commit_all
from tests.support.skill_scripts import fingerprint, image_checkout, load_skill_script

release = load_skill_script("role-manuals/scripts/release.py")
NAME = "2026-11-08_software-1.0.0_handleiding-2.0"
TODAY = dt.date(2026, 11, 9)


@pytest.mark.parametrize(
    ("tag", "same", "label"),
    [
        ("v0.6.0", True, "0.6.0"),
        ("v0.6.0-rc.35", True, "0.6.0-rc.35, testversie van 0.6.0"),
        (
            "v0.6.0-rc.50",
            False,
            "0.6.0-rc.50, testversie van 0.6.0, met latere wijzigingen (commit c0d18c0)",
        ),
        ("v0.7.1", False, "0.7.1, met latere wijzigingen (commit c0d18c0)"),
        ("", False, "gebouwd uit commit c0d18c0"),
    ],
)
def test_the_label_names_the_release_and_an_image_of_other_files(
    tag: str, same: bool, label: str
) -> None:
    """A release gives its number, a candidate its release, other image files their commit."""
    assert release.software_label(tag, "c0d18c01c354", same) == label


@pytest.fixture
def checkout(git: Git, tmp_path: Path) -> Path:
    """An image of ``app/`` tagged ``v1.0.0`` and, annotated, its candidate on the same commit.

    ``git describe`` prefers the annotated tag, so on its own it would name the candidate.
    """
    image_checkout(git, tmp_path)
    git("tag", "-a", "v1.0.0-rc.1", "-m", "candidate")
    git("tag", "v1.0.0")
    return tmp_path


@pytest.fixture
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A workspace whose baseline is the commit of an earlier version."""
    root = tmp_path_factory.mktemp("workspace")
    (root / "variables.json").write_text(json.dumps({"product": "de dienst", "commit": "d36485a6"}))
    return root


def _head(git: Git) -> str:
    return git("rev-parse", "HEAD").strip()


def _run(tmp_path_factory: pytest.TempPathFactory, commit: str, built: str) -> Path:
    """The stack.env that ``stack.sh up`` writes for a run built from ``commit``."""
    env = tmp_path_factory.mktemp("run") / "stack.env"
    env.write_text(
        f"VERIFY_PROJECT=demo\nBUILT_FROM_COMMIT={commit}\nBUILT_FROM_FINGERPRINT={built}\n"
    )
    return env


def test_a_release_names_its_commit_before_its_candidate(git: Git, checkout: Path) -> None:
    """On the release's commit and after it the release; in a repository without tags, nothing."""
    assert git("describe", "--tags", "--abbrev=0", _head(git)).strip() == "v1.0.0-rc.1"
    assert release.release_tag(checkout, _head(git)) == "v1.0.0"
    (checkout / "notes.md").write_text("later\n")
    commit_all(git, "notes")
    assert release.release_tag(checkout, _head(git)) == "v1.0.0"
    git("tag", "-d", "v1.0.0", "v1.0.0-rc.1")
    assert release.release_tag(checkout, _head(git)) == ""


def test_record_names_the_release_when_the_image_is_the_tags(
    git: Git, checkout: Path, workspace: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The label and the tag are written; the baseline stays, only freeze moves it."""
    run = _run(tmp_path_factory, _head(git), fingerprint(checkout).stdout.strip())
    assert release.record(workspace, run, repo=checkout) == "1.0.0"
    assert json.loads((workspace / "variables.json").read_text()) == {
        "product": "de dienst",
        "commit": "d36485a6",
        "software_version": "1.0.0",
        "release": "v1.0.0",
    }


def test_record_calls_a_run_of_other_image_files_a_trial(
    git: Git, checkout: Path, workspace: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """An image file changed after the tag: the label names the commit and no release is set."""
    (checkout / "app" / "main.py").write_text("VALUE = 2\n")
    commit_all(git, "app")
    run = _run(tmp_path_factory, _head(git), fingerprint(checkout).stdout.strip())
    label = release.record(workspace, run, repo=checkout)
    assert label == f"1.0.0, met latere wijzigingen (commit {_head(git)[:7]})"
    assert json.loads((workspace / "variables.json").read_text())["release"] == ""


@pytest.mark.parametrize("built", ["", "not-a-fingerprint"])
def test_record_refuses_a_run_without_its_image_fingerprint(
    git: Git,
    checkout: Path,
    workspace: Path,
    tmp_path_factory: pytest.TempPathFactory,
    built: str,
) -> None:
    """An empty fingerprint never matches the tag's empty one: the record is refused."""
    run = _run(tmp_path_factory, _head(git), built)
    with pytest.raises(SystemExit, match="records no commit and image fingerprint"):
        release.record(workspace, run, repo=checkout)


def _checked(workspace: Path, blocking: int = 0) -> None:
    """Write the report of check.py, later than everything it checked."""
    report = workspace / "evidence" / "check.md"
    report.write_text(f"# Controle van de handleidingen\n\nBlokkerende bevindingen: {blocking}.\n")
    later = time.time() + 60
    os.utime(report, (later, later))


def _settled(workspace: Path, version: str = "2.0, vastgesteld op 9 november 2026") -> Path:
    """The workspace after update.sh on ``v1.0.0``, a person's settlement, a build and a check."""
    data = json.loads((workspace / "variables.json").read_text())
    data |= {"release": "v1.0.0", "software_version": "1.0.0"}
    (workspace / "variables.json").write_text(json.dumps(data))
    for folder in ("build", "sources", "img/previous", "evidence/previous"):
        (workspace / folder).mkdir(parents=True, exist_ok=True)
    files = {
        "sources/10-planner.md": "# Planner\n",
        "img/pl-01.png": "png",
        "img/previous/pl-01.png": "earlier",
        "build/10-planner.pdf": "%PDF",
        "evidence/previous/state-1.json": "{}",
        "evidence/state.json": json.dumps({"started": "2026-11-08 21:47:02 CET"}),
        "evidence/afhechting.md": "# Afhechting\n",
        "style-guide.md": "# Stijl\n",
        "README.md": "# Werkmap\n",
    }
    for name, text in files.items():
        (workspace / name).write_text(text)
    manuals = [
        {"file": "00-aanpak", "version": version},
        {"file": "10-planner", "version": version},
    ]
    (workspace / "build" / "manuals.json").write_text(json.dumps({"manuals": manuals}))
    _checked(workspace)
    return workspace


def _manifest(folder: Path) -> dict[str, str]:
    lines = (folder / "MANIFEST.sha256").read_text().splitlines()
    return {path: digest for digest, path in (line.split("  ", 1) for line in lines)}


def test_a_settled_version_is_frozen_whole_read_only_and_checkable(
    git: Git, checkout: Path, workspace: Path
) -> None:
    """Every file but the previous capture and README.md, a manifest, a zip, a new baseline."""
    target = release.freeze(_settled(workspace), repo=checkout, today=TODAY)
    versions = workspace / "versies"
    assert target == versions / NAME
    listed = _manifest(target)
    files = {f"./{p.relative_to(target).as_posix()}" for p in target.rglob("*") if p.is_file()}
    assert set(listed) == files - {"./MANIFEST.sha256"}
    for path, digest in listed.items():
        assert hashlib.sha256((target / path).read_bytes()).hexdigest() == digest
    assert {"./img/pl-01.png", "./style-guide.md", "./LEESMIJ.md"} <= set(listed)
    assert not [p for p in listed if "previous" in p or p == "./README.md"]
    readme = (target / "LEESMIJ.md").read_text()
    for text in (
        "# Handleidingen versie 2.0",
        "**Versie:** 2.0, vastgesteld op 9 november 2026.",
        "**Software:** 1.0.0; tag `v1.0.0`",
        "op 8 november 2026 opgenomen",
        "**Bevroren:** 9 november 2026.",
    ):
        assert text in readme
    assert not [p for p in [target, *target.rglob("*")] if p.stat().st_mode & 0o222]
    archive = versions / f"{NAME}.zip"
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert (versions / f"{NAME}.zip.sha256").read_text() == f"{digest}  {NAME}.zip\n"
    with zipfile.ZipFile(archive) as bundle:
        assert sorted(bundle.namelist()) == sorted(f"{NAME}/{p[2:]}" for p in files)
    baseline = json.loads((workspace / "variables.json").read_text())["commit"]
    assert baseline == git("rev-parse", "v1.0.0^{commit}").strip()[:12]
    assert sorted(p.name for p in versions.iterdir()) == [NAME, f"{NAME}.zip", f"{NAME}.zip.sha256"]


def _change(workspace: Path, change: str) -> None:
    if change == "trial":
        data = json.loads((workspace / "variables.json").read_text())
        (workspace / "variables.json").write_text(json.dumps(data | {"release": ""}))
    elif change == "exists":
        (workspace / "versies" / NAME).mkdir(parents=True)
    elif change in ("versions differ", "concept"):
        versions = (
            ["2.0, vastgesteld", "2.1"] if change == "versions differ" else ["0.1 (concept)"] * 2
        )
        manuals = [{"file": f"{i}0-x", "version": v} for i, v in enumerate(versions)]
        (workspace / "build" / "manuals.json").write_text(json.dumps({"manuals": manuals}))
    elif change == "no record":
        (workspace / "evidence" / "afhechting.md").unlink()
    if change == "blocking check":
        _checked(workspace, blocking=2)
    elif change == "edited after the check":
        source = workspace / "sources" / "10-planner.md"
        later = (workspace / "evidence" / "check.md").stat().st_mtime + 60
        os.utime(source, (later, later))
    else:
        _checked(workspace)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("trial", "is a trial"),
        ("exists", "exists already"),
        ("versions differ", "no one version number"),
        ("concept", "no one version number"),
        ("no record", "afhechting.md first"),
        ("blocking check", "missing or blocks"),
        ("edited after the check", "changed after the last check: 10-planner.md"),
    ],
)
def test_freeze_refuses_what_is_no_settled_release(
    checkout: Path, workspace: Path, change: str, message: str
) -> None:
    """Nothing is copied and the baseline stays when a version is not ready or exists."""
    _settled(workspace)
    _change(workspace, change)
    before = (workspace / "variables.json").read_text()
    with pytest.raises(SystemExit, match=message):
        release.freeze(workspace, repo=checkout, today=TODAY)
    assert (workspace / "variables.json").read_text() == before
    left = sorted(p.name for p in (workspace / "versies").glob("*")) if change == "exists" else []
    assert left == ([NAME] if change == "exists" else [])
    assert change == "exists" or not (workspace / "versies").exists()


def test_a_failed_freeze_leaves_no_half_version(
    checkout: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure while zipping removes the copy; nothing in versies/ and the baseline stays."""
    _settled(workspace)
    before = (workspace / "variables.json").read_text()

    def full(*args: object, **kwargs: object) -> None:
        raise OSError("no space left")

    monkeypatch.setattr(release.zipfile, "ZipFile", full)
    with pytest.raises(OSError, match="no space left"):
        release.freeze(workspace, repo=checkout, today=TODAY)
    assert list((workspace / "versies").iterdir()) == []
    assert (workspace / "variables.json").read_text() == before
