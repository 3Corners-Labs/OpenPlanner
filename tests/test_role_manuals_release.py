"""The release a capture shows and the version frozen per release (role-manuals' release.py).

The title pages say which software the manuals show: the label comes from the capture's own
record and the release tags, never from ``pyproject.toml``, which names the previous release
until the next one is cut. A frozen version is whole, read-only and checkable, or it is not
there at all.
"""

import datetime as dt
import hashlib
import json
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
    (root / "evidence").mkdir()
    (root / "variables.json").write_text(json.dumps({"product": "de dienst", "commit": "d36485a6"}))
    return root


def _head(git: Git) -> str:
    return git("rev-parse", "HEAD").strip()


def _captured(workspace: Path, build: dict[str, str]) -> None:
    """Write the capture's record as capture.mjs does: its start, the run's build, its shots."""
    state = {
        "started": "2026-11-08 21:47:02 CET",
        "stack": {"id": "20261108-203309", "build": build},
        "records": {"pl-01": {"id": "pl-01", "status": "captured"}},
    }
    (workspace / "evidence" / "state.json").write_text(json.dumps(state))


def _captured_here(workspace: Path, git: Git, checkout: Path) -> None:
    """A capture of a stack built from the checkout as it is now."""
    built = fingerprint(checkout).stdout.strip()
    _captured(workspace, {"commit": _head(git), "fingerprint": built})


def test_a_release_names_its_commit_before_its_candidate(git: Git, checkout: Path) -> None:
    """On the release's commit and after it the release; in a repository without tags, nothing."""
    assert git("describe", "--tags", "--abbrev=0", _head(git)).strip() == "v1.0.0-rc.1"
    assert release.release_tag(checkout, _head(git)) == "v1.0.0"
    (checkout / "notes.md").write_text("later\n")
    commit_all(git, "notes")
    assert release.release_tag(checkout, _head(git)) == "v1.0.0"
    git("tag", "-d", "v1.0.0", "v1.0.0-rc.1")
    assert release.release_tag(checkout, _head(git)) == ""


def test_a_tag_of_another_shape_is_passed_over(git: Git, checkout: Path) -> None:
    """Only ``vX.Y.Z`` and ``vX.Y.Z-rc.N`` name a release; a nearer tag of another shape none."""
    (checkout / "notes.md").write_text("later\n")
    commit_all(git, "notes")
    git("tag", "v2-preview")
    assert release.release_tag(checkout, _head(git)) == "v1.0.0"
    git("tag", "-d", "v1.0.0", "v1.0.0-rc.1")
    assert release.release_tag(checkout, _head(git)) == ""


def test_a_workspace_inside_a_checkout_is_refused(git: Git, checkout: Path) -> None:
    """record and freeze write into the workspace: never into a checkout of the software."""
    (checkout / ".claude" / "skills" / "role-manuals").mkdir(parents=True)
    inside = checkout / "manuals"
    (inside / "evidence").mkdir(parents=True)
    for command in (release.record, release.freeze):
        with pytest.raises(SystemExit, match="inside the checkout"):
            command(inside, repo=checkout)


def test_record_names_the_release_when_the_image_is_the_tags(
    git: Git, checkout: Path, workspace: Path
) -> None:
    """The label goes to software_version; the baseline stays, only freeze moves it."""
    _captured_here(workspace, git, checkout)
    assert release.record(workspace, repo=checkout) == "1.0.0"
    assert json.loads((workspace / "variables.json").read_text()) == {
        "product": "de dienst",
        "commit": "d36485a6",
        "software_version": "1.0.0",
    }


def test_record_calls_a_capture_of_other_image_files_a_trial(
    git: Git, checkout: Path, workspace: Path
) -> None:
    """An image file changed after the tag: the label names the commit of the capture."""
    (checkout / "app" / "main.py").write_text("VALUE = 2\n")
    commit_all(git, "app")
    _captured_here(workspace, git, checkout)
    label = release.record(workspace, repo=checkout)
    assert label == f"1.0.0, met latere wijzigingen (commit {_head(git)[:7]})"


@pytest.mark.parametrize("built", ["", "not-a-fingerprint", None])
def test_record_refuses_a_capture_without_its_image_fingerprint(
    git: Git, checkout: Path, workspace: Path, built: str | None
) -> None:
    """A capture by an older skill records no fingerprint; an empty one never matches."""
    build = {"commit": _head(git)} | ({} if built is None else {"fingerprint": built})
    _captured(workspace, build)
    with pytest.raises(SystemExit, match="records no commit and image fingerprint"):
        release.record(workspace, repo=checkout)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _checked(workspace: Path, blocking: int = 0) -> None:
    """Write the verdict of check.py: the blocking count and the hash of every file it read."""
    names = ["variables.json", "build/manuals.json", "evidence/shots.json"]
    names += [f"sources/{p.name}" for p in sorted((workspace / "sources").glob("*.md"))]
    inputs = {name: _sha256(workspace / name) for name in names}
    verdict = {"blocking": blocking, "inputs": inputs}
    (workspace / "evidence" / "check.json").write_text(json.dumps(verdict))


def _settled(
    workspace: Path, git: Git, checkout: Path, version: str = "2.0, vastgesteld op 9 november 2026"
) -> Path:
    """The workspace after update.sh on ``v1.0.0``, a person's settlement, a build and a check."""
    _captured_here(workspace, git, checkout)
    release.record(workspace, repo=checkout)
    for folder in ("build", "sources", "img/previous", "evidence/previous"):
        (workspace / folder).mkdir(parents=True, exist_ok=True)
    files = {
        "sources/10-planner.md": "# Planner\n",
        "img/pl-01.png": "png",
        "img/previous/pl-01.png": "earlier",
        "build/10-planner.pdf": "%PDF",
        "evidence/previous/state-1.json": "{}",
        "evidence/shots.json": json.dumps([{"id": "pl-01"}]),
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
    """Every file but the previous capture and README.md, a manifest, a zip, a new baseline.

    The release tag is annotated, as the release app makes them: the baseline is its commit.
    """
    git("tag", "-d", "v1.0.0")
    git("tag", "-a", "v1.0.0", "-m", "release")
    target = release.freeze(_settled(workspace, git, checkout), repo=checkout, today=TODAY)
    versions = workspace / "versies"
    assert target == versions / NAME
    listed = _manifest(target)
    files = {f"./{p.relative_to(target).as_posix()}" for p in target.rglob("*") if p.is_file()}
    assert set(listed) == files - {"./MANIFEST.sha256"}
    for path, digest in listed.items():
        assert _sha256(target / path) == digest
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
    assert (versions / f"{NAME}.zip.sha256").read_text() == f"{_sha256(archive)}  {NAME}.zip\n"
    with zipfile.ZipFile(archive) as bundle:
        assert sorted(bundle.namelist()) == sorted(f"{NAME}/{p[2:]}" for p in files)
    baseline = json.loads((workspace / "variables.json").read_text())["commit"]
    assert baseline == _head(git)[:12]
    assert sorted(p.name for p in versions.iterdir()) == [NAME, f"{NAME}.zip", f"{NAME}.zip.sha256"]


def _change(workspace: Path, change: str) -> None:
    """Undo one part of a settled workspace; the check stays current unless that is the change."""
    variables = workspace / "variables.json"
    if change == "trial":
        state = json.loads((workspace / "evidence" / "state.json").read_text())
        state["stack"]["build"]["fingerprint"] = "0" * 64
        (workspace / "evidence" / "state.json").write_text(json.dumps(state))
    elif change == "another label":
        variables.write_text(
            json.dumps(json.loads(variables.read_text()) | {"software_version": "0.9.0"})
        )
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
    elif change == "earlier image":
        (workspace / "img" / "pl-02.png").write_text("from the capture before")
    if change == "blocking check":
        _checked(workspace, blocking=2)
    elif change == "edited after the check":
        (workspace / "sources" / "10-planner.md").write_text("# Planner, bijgewerkt\n")
    elif change == "added after the check":
        (workspace / "sources" / "20-medewerker.md").write_text("# Medewerker\n")
    elif change == "shot list changed after the check":
        (workspace / "evidence" / "shots.json").write_text(json.dumps([{"id": "pl-09"}]))
    elif change == "verdict without the variables":
        path = workspace / "evidence" / "check.json"
        verdict = json.loads(path.read_text())
        del verdict["inputs"]["variables.json"]
        path.write_text(json.dumps(verdict))
    else:
        _checked(workspace)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("trial", "is a trial"),
        ("another label", "the title pages say '0.9.0', the capture shows '1.0.0'"),
        ("exists", "exists already"),
        ("versions differ", "no one version number"),
        ("concept", "no one version number"),
        ("no record", "afhechting.md first"),
        ("earlier image", "not taken by this capture, so from an earlier one: pl-02;"),
        ("blocking check", "found 2 blocking"),
        ("edited after the check", "not what the last check read: sources/10-planner.md"),
        ("added after the check", "not what the last check read: sources/20-medewerker.md"),
        ("shot list changed after the check", "not what the last check read: evidence/shots.json"),
        ("verdict without the variables", "not what the last check read: variables.json;"),
    ],
)
def test_freeze_refuses_what_is_no_settled_release(
    git: Git, checkout: Path, workspace: Path, change: str, message: str
) -> None:
    """Nothing is copied and the baseline stays when a version is not ready or exists."""
    _settled(workspace, git, checkout)
    _change(workspace, change)
    before = (workspace / "variables.json").read_text()
    with pytest.raises(SystemExit, match=message):
        release.freeze(workspace, repo=checkout, today=TODAY)
    assert (workspace / "variables.json").read_text() == before
    left = sorted(p.name for p in (workspace / "versies").glob("*")) if change == "exists" else []
    assert left == ([NAME] if change == "exists" else [])
    assert change == "exists" or not (workspace / "versies").exists()


def test_a_failed_freeze_leaves_no_half_version(
    git: Git, checkout: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure while zipping removes the copy; nothing in versies/ and the baseline stays."""
    _settled(workspace, git, checkout)
    before = (workspace / "variables.json").read_text()

    def full(*args: object, **kwargs: object) -> None:
        raise OSError("no space left")

    monkeypatch.setattr(release.zipfile, "ZipFile", full)
    with pytest.raises(OSError, match="no space left"):
        release.freeze(workspace, repo=checkout, today=TODAY)
    assert list((workspace / "versies").iterdir()) == []
    assert (workspace / "variables.json").read_text() == before
