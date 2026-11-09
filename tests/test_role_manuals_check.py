"""The pure parts of the role-manuals check (``.claude/skills/role-manuals/scripts/check.py``).

The script runs on an organisation's workspace, outside CI; these tests keep its reading of
labels, its rename, its matrix mapping and its rights check honest.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

from tests.support.skill_scripts import load_skill_script

check = load_skill_script("role-manuals/scripts/check.py")


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


RIGHTS = check.Rights(
    roles={"beheerder": ["org_admin"], "planner": ["group_admin"], "teamleider": []},
    matrix={
        "org_admin": frozenset({"groups.manage", "reports.view", "staff.view"}),
        "group_admin": frozenset({"reports.view"}),
    },
    participant=frozenset({"participant.book", "participant.booking_cancel"}),
)
"""A small matrix: two roles, a role word without roles, and two participant actions."""


def _manual(file: str, role: str, *permissions: str) -> dict:
    """A manual as build/manuals.json lists it, with one task per permission from line 10 on."""
    tasks = [
        {"permission": code, "line": 10 + i, "screen": "participant:group-page"}
        for i, code in enumerate(permissions)
    ]
    return {"file": file, "role": role, "tasks": tasks, "figures": [], "bold": []}


@pytest.mark.parametrize(
    ("manual", "blocked"),
    [
        (_manual("10-beheerder", "Beheerder", "groups.manage"), False),
        (_manual("20-planner", "Planner", "groups.manage"), True),
        (_manual("30-teamleider", "Teamleider", "reports.view"), True),
        (_manual("40-deelnemers", "Deelnemers", "reports.view"), True),
        (_manual("00-aanpak", "Projectleiding", "participant.book"), True),
    ],
)
def test_a_task_needs_a_right_its_readers_hold(manual: dict, blocked: bool) -> None:
    """The same rights as the rights check: none for a role word without roles or the approach."""
    ctx = {"rights": RIGHTS, "codes": {"groups.manage", "reports.view", "participant.book"}}
    ctx |= {"topics": set(), "shots": set(), "used": set(), "tasks": 0}
    report = check.Report()
    check.check_tasks(manual, ctx, report)
    assert report.blocking == int(blocked)


def test_a_right_without_a_task_blocks_unless_a_reason_is_given() -> None:
    """A task in any manual describes a right; a blank reason is none; the approach holds none."""
    manuals = [
        _manual("00-aanpak", "Projectleiding"),
        _manual("10-beheerder", "Beheerder", "groups.manage"),
        _manual("20-planner", "Planner", "reports.view"),
        _manual("40-deelnemers", "Deelnemers", "participant.book"),
    ]
    reasons = {"staff.view": "doet de leverancier", "participant.booking_cancel": " "}
    report = check.Report()
    check.check_rights(manuals, RIGHTS, reasons, report)
    assert report.blocking == 1
    assert report.sections["Rechten"] == [
        "5 rechten van de rollen in de handleidingen: 3 met een taak, 1 bewust zonder taak, "
        "1 zonder taak of reden.",
        "**Blokkerend:** `participant.booking_cancel` (Deelnemers) heeft geen taak; de reden in "
        "`rights_without_task` is leeg of geen tekst.",
        "`staff.view` (Beheerder) bewust zonder taak: doet de leverancier",
    ]


def test_a_reason_for_no_right_or_for_a_right_with_a_task_is_reported() -> None:
    """The list of reasons stays true: an entry no role here holds or a task covers goes."""
    reasons = {"reports.view": "staat al in een taak", "groups.manage": "niet van de planner"}
    report = check.Report()
    check.check_rights([_manual("20-planner", "Planner", "reports.view")], RIGHTS, reasons, report)
    assert report.blocking == 0
    assert report.sections["Rechten"][1:] == [
        "`groups.manage` in `rights_without_task` is geen recht van een rol in deze "
        "handleidingen; haal het weg.",
        "`reports.view` in `rights_without_task` heeft een taak (20-planner.md:10); haal het weg.",
    ]


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
    # The manual's empty role makes it the participants' manual; its rights have no task here.
    reasons = {action.key: "niet in deze test" for action in check.PARTICIPANT_ACTIONS}
    root = _built_workspace(
        tmp_path,
        {"product": "Acme-planner", "rights_without_task": reasons},
        {"10-planner": "**Acme-planner** en **Bestaat-niet-knop**\n"},
        bold=[{"text": "Acme-planner", "line": 1}, {"text": "Bestaat-niet-knop", "line": 1}],
    )
    monkeypatch.setattr(sys, "argv", ["check.py", str(root)])
    assert check.main() == 0
    report = (root / "evidence" / "check.md").read_text()
    assert "**Bestaat-niet-knop** komt niet voor in de software" in report
    assert "**Acme-planner**" not in report


def test_the_verdict_holds_the_hash_of_every_file_the_check_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """evidence/check.json: the blocking count and each input by its path in the workspace."""
    root = _built_workspace(tmp_path, {"product": "de dienst"}, {"00-aanpak": "# Aanpak\n"})
    monkeypatch.setattr(sys, "argv", ["check.py", str(root)])
    assert check.main() == 0
    files = ["variables.json", "build/manuals.json", "evidence/shots.json", "sources/00-aanpak.md"]
    inputs = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}
    verdict = json.loads((root / "evidence" / "check.json").read_text())
    assert verdict == {"blocking": 0, "inputs": inputs}


def test_rights_without_task_must_map_a_right_to_a_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A list of rights without reasons is refused before any report is written."""
    root = _built_workspace(tmp_path, {"rights_without_task": ["staff.view"]}, {"10-x": "# X\n"})
    monkeypatch.setattr(sys, "argv", ["check.py", str(root)])
    with pytest.raises(SystemExit, match="maps a right to a reason"):
        check.main()
    assert not (root / "evidence" / "check.md").exists()


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
    """Opening a workspace follows a symbolic link before it looks for a checkout above it."""
    assert check.Workspace(tmp_path).evidence == tmp_path.resolve() / "evidence"
    link = tmp_path / "manuals"
    link.symlink_to(check.REPO / "docs")
    with pytest.raises(SystemExit, match="inside the checkout"):
        check.Workspace(link)
