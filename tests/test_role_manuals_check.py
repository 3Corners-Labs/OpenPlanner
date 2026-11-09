"""The pure parts of the role-manuals check (``.claude/skills/role-manuals/scripts/check.py``).

The script runs on an organisation's workspace, outside CI; these tests keep its reading of
labels, its rename and its matrix mapping honest.
"""

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".claude/skills/role-manuals/scripts/check.py"
_spec = importlib.util.spec_from_file_location("role_manuals_check", SCRIPT)
assert _spec is not None
assert _spec.loader is not None
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def test_a_bold_span_gives_its_labels_and_a_path_its_parts() -> None:
    """A path gives its parts; structure words, numbers and a lead-in with a colon are no label."""
    assert check.labels_of("Planning → Bezetting.") == ["Planning", "Bezetting"]
    assert check.labels_of("Om nieuwe   tijd vragen") == ["Om nieuwe tijd vragen"]
    assert check.labels_of("Tip:") == []
    assert check.labels_of("Stap 2") == []
    assert check.labels_of("15 minuten") == []


def test_labels_carry_the_line_the_build_found() -> None:
    """The labels of a manual come from the bold spans of build/manuals.json, with their lines."""
    manual = {"bold": [{"text": "Groepen → demo", "line": 12}, {"text": "Opslaan", "line": 30}]}
    assert check.labels(manual) == [(12, "Groepen"), (12, "demo"), (30, "Opslaan")]


def test_a_renamed_label_is_replaced_only_as_a_whole_part() -> None:
    """The first part of a path, a label with a full stop and a wrapped label are replaced."""
    text = (
        "**Instellingen → Boekingsformulier**, **Agenda**. **Agenda week**, "
        "**Agendaoverzicht** en **Om nieuwe\ntijd vragen**."
    )
    renamed = {
        "Instellingen": "Configuratie",
        "Agenda": "Afspraken",
        "Om nieuwe tijd vragen": "Opnieuw plannen",
    }
    assert check.rename_labels(text, renamed) == (
        "**Configuratie → Boekingsformulier**, **Afspraken**. **Agenda week**, "
        "**Agendaoverzicht** en **Opnieuw plannen**."
    )


def test_a_label_matches_the_catalogue_exactly_by_placeholder_or_as_part() -> None:
    """Closing punctuation is ignored; placeholders and an ellipsis match; parts are reported."""
    catalogue = check.Catalogue(
        {"a": "Opslaan.", "b": "Afspraak {reference} verplaatsen", "c": "Wachtrij in mijn groepen"},
        ["Webex Meetings"],
    )
    assert catalogue.match("Opslaan") == "exact"
    assert catalogue.match("Webex Meetings") == "exact"
    assert catalogue.match("Afspraak OP-000001 verplaatsen") == "placeholder"
    assert catalogue.match("Wachtrij in …") == "placeholder"
    assert catalogue.match("mijn groepen") == "in tekst"
    assert catalogue.match("Bestaat niet") == "onbekend"


def test_a_manual_maps_to_its_roles_by_a_word_of_its_role() -> None:
    """Beheerder, planner and medewerker have matrix roles; any other manual is for participants."""
    roles = {word: [r.value for r in rs] for word, rs in check.DEFAULT_ROLES.items()}
    assert check.manual_roles("Beheerder", roles) == ["superuser", "org_admin", "auditor"]
    assert check.manual_roles("Handleiding voor de planner", roles) == ["group_admin"]
    assert check.manual_roles("Deelnemers", roles) is None


def _built_workspace(
    root: Path,
    variables: dict,
    sources: dict[str, str] | None = None,
    bold: list[dict] | None = None,
) -> Path:
    """Write a workspace and a build/manuals.json current for it, as build.mjs writes one."""
    for folder in ("sources", "evidence", "build"):
        (root / folder).mkdir(exist_ok=True)
    (root / "variables.json").write_text(json.dumps(variables))
    (root / "evidence" / "shots.json").write_text("[]")
    for stem, text in (sources or {}).items():
        (root / "sources" / f"{stem}.md").write_text(text)
    manuals = [
        {
            "file": path.stem,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "role": "",
            "tasks": [],
            "figures": [],
            "bold": bold or [],
        }
        for path in sorted((root / "sources").glob("*.md"))
    ]
    digest = hashlib.sha256((root / "variables.json").read_bytes()).hexdigest()
    (root / "build" / "manuals.json").write_text(
        json.dumps({"variables_sha256": digest, "manuals": manuals})
    )
    return root


def test_a_current_build_is_read_with_its_inputs(tmp_path: Path) -> None:
    """The variables, the sources and the manuals of a current build come back."""
    root = _built_workspace(tmp_path, {"product": "de dienst"}, {"10-planner": "# Planner\n"})
    inputs = check.load_inputs(check.Workspace(root))
    assert inputs.config == {"product": "de dienst"}
    assert [m["file"] for m in inputs.manuals] == ["10-planner"]
    assert list(inputs.sources.values()) == ["# Planner\n"]


@pytest.mark.parametrize(
    ("change", "named"),
    [
        ("added", "20-medewerker"),
        ("removed", "10-planner"),
        ("edited", "10-planner"),
        ("variables", "variables.json"),
        ("old format", "another build.mjs"),
    ],
)
def test_a_build_of_other_inputs_is_refused_naming_what_changed(
    tmp_path: Path, change: str, named: str
) -> None:
    """A source added, removed or edited, edited variables or an older build file is refused."""
    root = _built_workspace(tmp_path, {"product": "de dienst"}, {"10-planner": "# Planner\n"})
    sources = root / "sources"
    if change == "added":
        (sources / "20-medewerker.md").write_text("# Medewerker\n")
    elif change == "removed":
        (sources / "10-planner.md").unlink()
    elif change == "edited":
        (sources / "10-planner.md").write_text("# Planner, bijgewerkt\n")
    elif change == "variables":
        (root / "variables.json").write_text(json.dumps({"product": "iets anders"}))
    else:
        (root / "build" / "manuals.json").write_text("[]")
    with pytest.raises(SystemExit, match=re.escape(named)):
        check.load_inputs(check.Workspace(root))


def test_a_value_of_the_variables_is_a_known_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The organisation's word for the service, in bold, is no unknown screen text."""
    root = _built_workspace(
        tmp_path,
        {"product": "Acme-planner"},
        {"10-planner": "**Acme-planner** en **Bestaat-niet-knop**\n"},
        bold=[{"text": "Acme-planner", "line": 1}, {"text": "Bestaat-niet-knop", "line": 1}],
    )
    monkeypatch.setattr(sys, "argv", ["check.py", str(root)])
    assert check.main() == 0
    report = (root / "evidence" / "check.md").read_text()
    assert "**Bestaat-niet-knop** komt niet voor in de software" in report
    assert "**Acme-planner**" not in report


def test_only_a_commit_hash_is_passed_to_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--since and the stored commit are refused unless they are a hash; git is never run."""
    assert check.commit_hash("d36485a6ee78") == "d36485a6ee78"
    with pytest.raises(argparse.ArgumentTypeError):
        check.commit_hash("--output=/tmp/x")
    _built_workspace(tmp_path, {"commit": "HEAD~1"})

    def no_git(*args: str) -> str:
        raise AssertionError(f"git was called with {args}")

    monkeypatch.setattr(check, "git", no_git)
    monkeypatch.setattr(sys, "argv", ["check.py", str(tmp_path)])
    with pytest.raises(SystemExit, match="no commit hash"):
        check.main()
    for since in ("--since=--output=/tmp/x", "--since=HEAD~1"):
        monkeypatch.setattr(sys, "argv", ["check.py", str(tmp_path), since])
        with pytest.raises(SystemExit):
            check.main()
        assert "is no commit hash" in capsys.readouterr().err


def test_a_workspace_inside_a_checkout_is_refused_also_through_a_link(tmp_path: Path) -> None:
    """The guard follows a symbolic link before it looks for a checkout above the folder."""
    check.guard_outside_checkout(tmp_path)
    link = tmp_path / "manuals"
    link.symlink_to(check.REPO / "docs")
    with pytest.raises(SystemExit, match="inside the checkout"):
        check.guard_outside_checkout(link)
