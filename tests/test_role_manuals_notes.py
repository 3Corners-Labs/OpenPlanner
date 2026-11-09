"""The release notes of the role manuals: what a release changed per role, and the gate on them.

``release.py notes`` sorts the commits between the last frozen version and the capture's release
by the roles they touch; the document that tells readers what is new is judged against them
(``release_notes.py``, ``check.py``), and ``freeze`` refuses release notes of another range.
These tests keep the sorting, the judging and the files written honest.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests.support.git_repository import Git, commit_all
from tests.support.skill_scripts import SKILLS, load_skill_script, manuals_workspace

notes = load_skill_script("role-manuals/scripts/release_notes.py")
release = load_skill_script("role-manuals/scripts/release.py")
check = load_skill_script("role-manuals/scripts/check.py")
TEMPLATE = SKILLS / "role-manuals" / "templates" / "wat-is-nieuw.md"
DOCUMENT = "90-wat-is-nieuw"


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("feat(planner): hold a booking (#10)", ("feat", "planner", "hold a booking", "10")),
        ("fix: a typo", ("fix", "", "a typo", "")),
        ("Feat(Admin)!: breaking (#7)", ("feat", "Admin", "breaking", "7")),
        ('Revert "feat: x"', ("other", "", 'Revert "feat: x"', "")),
        ("Merge branch 'main'", ("other", "", "Merge branch 'main'", "")),
    ],
)
def test_a_subject_gives_its_type_scope_text_and_pull_request(
    subject: str, expected: tuple[str, str, str, str]
) -> None:
    """A Conventional Commit is read whole; anything else is ``other`` and keeps its text."""
    assert notes.parse_subject(subject) == expected


PLACES = {"group-agenda": [("Planner", "20-planner.md:7 › De agenda lezen")]}


def _commit(number: str, subject: str, *files: str) -> dict[str, object]:
    return {"hash": f"{number:0>40}", "subject": subject, "files": list(files)}


def test_a_help_topic_or_a_participant_page_places_a_change_with_a_role() -> None:
    """The topic of a screen reaches the roles whose tasks use it; participant pages theirs."""
    changes = notes.classify(
        [
            _commit("a", "feat(planner): hold (#10)", "docs/manual/nl/group-agenda.md", "x.py"),
            _commit("b", "fix(booking): zone (#11)", "app/api/templates/book.html"),
            _commit("c", "fix(widget): focus (#12)", "widget/src/calendar.js"),
            _commit("d", "feat(admin): thing (#13)", "app/admin/routes.py"),
            _commit("e", "feat(admin): orphan topic (#14)", "docs/manual/nl/unused.md"),
            _commit("f", "test(domain): more (#15)", "docs/manual/nl/group-agenda.md"),
            {"hash": "1234567" + "0" * 33, "subject": "Merge branch 'main'", "files": ["a.py"]},
        ],
        PLACES,
        "Deelnemers",
    )
    assert [(c.id, c.visible, c.roles, c.topics) for c in changes] == [
        ("#10", True, ("Planner",), ("group-agenda",)),
        ("#11", True, ("Deelnemers",), ()),
        ("#12", True, ("Deelnemers",), ()),
        ("#13", True, (), ()),
        ("#14", True, (), ("unused",)),
        ("#15", False, ("Planner",), ("group-agenda",)),
        ("c:1234567", False, (), ()),
    ]
    assert changes[0].places == ("Planner: 20-planner.md:7 › De agenda lezen",)


def test_a_change_without_a_pull_request_is_named_by_its_short_hash() -> None:
    """A direct commit has no number; ``c:`` and its first seven characters name it."""
    direct = {"hash": "abcdef1" + "0" * 33, "subject": "fix: x", "files": []}
    [change] = notes.classify([direct], {}, "Deelnemers")
    assert change.id == "c:abcdef1"


def test_the_result_lists_every_change_once_per_role_and_the_rest_apart() -> None:
    """Roles in the given order, a change under each role it touches, the rest never lost."""
    changes = notes.classify(
        [
            _commit("a", "feat(planner): hold (#10)", "docs/manual/nl/group-agenda.md"),
            _commit("b", "fix(booking): zone (#11)", "app/api/templates/book.html"),
            _commit("c", "feat(admin): thing (#13)", "app/admin/routes.py"),
            _commit("d", "test(domain): more (#15)"),
            _commit("e", "chore(ci): pins (#16)"),
        ],
        PLACES,
        "Deelnemers",
    )
    text = notes.render(
        changes,
        start="1.0.0 (aaaaaaaaaaaa)",
        end="1.1.0 (bbbbbbbbbbbb)",
        changelog="### Features\n\n- A thing.",
        roles=["Beheerder", "Planner", "Deelnemers"],
    )
    sections = dict(re.findall(r"^## (.+?)\n(.*?)(?=^## |\Z)", text, re.S | re.M))
    assert "Van **1.0.0 (aaaaaaaaaaaa)** naar **1.1.0 (bbbbbbbbbbbb)**: 5 wijzigingen" in text
    assert "waarvan 3 die een gebruiker kan merken" in text
    assert "Geen wijzigingen die voor deze rol zijn gevonden." in sections["Beheerder"]
    assert "hold (#10)" in sections["Planner"]
    assert "  - Taken: 20-planner.md:7 › De agenda lezen" in sections["Planner"]
    assert "Taken:" not in sections["Deelnemers"]
    assert "zone (#11)" in sections["Deelnemers"]
    assert "thing (#13)" in sections["Nog te plaatsen"]
    assert (
        "2 wijzigingen zonder zichtbaar gevolg: 1 chore, 1 test."
        in sections["Technisch, niet voor de handleidingen"]
    )
    assert "- A thing." in sections["Uit CHANGELOG.md van het project"]


def _changes() -> list:
    return notes.classify(
        [
            _commit("a", "feat(planner): hold (#10)", "docs/manual/nl/group-agenda.md"),
            _commit("b", "fix(booking): zone (#11)", "app/api/templates/book.html"),
            _commit("c", "feat(admin): thing (#13)"),
            _commit("d", "test(domain): more (#15)"),
        ],
        PLACES,
        "Deelnemers",
    )


FILLED = """# Wat is er nieuw

Je kunt een afspraak vasthouden.

<!-- bron: #10 -->

Een datum toont in de tijdzone van de groep.

<!-- bron: #11, #13 -->
"""


def test_a_document_that_names_every_change_leaves_nothing_open() -> None:
    """A source, a left-out change with its reason, and a reason that looks like a hash."""
    verdict = notes.judge(_changes(), FILLED)
    assert (verdict.unknown, verdict.unhandled_features, verdict.unhandled_other) == ([], [], [])
    other = (
        FILLED.replace("#11, #13", "#13") + "<!-- niet opgenomen: #11 defaced niets te zien -->\n"
    )
    verdict = notes.judge(_changes(), other)
    assert (verdict.unknown, verdict.unhandled_features, verdict.unhandled_other) == ([], [], [])


def test_a_new_feature_must_be_named_or_left_out_and_another_change_is_listed() -> None:
    """A feature blocks, a fix is listed; a test commit is no business of the document."""
    verdict = notes.judge(_changes(), "# Wat is er nieuw\n\n<!-- bron: #10 -->\n")
    assert [c.id for c in verdict.unhandled_features] == ["#13"]
    assert [c.id for c in verdict.unhandled_other] == ["#11"]


def test_a_point_that_rests_on_another_release_is_unknown() -> None:
    """A number the release does not hold, left over from the version before, is reported."""
    verdict = notes.judge(
        _changes(), FILLED + "\n<!-- bron: #99, #10 -->\n<!-- niet opgenomen: #98 -->\n"
    )
    assert verdict.unknown == ["#98", "#99"]


def test_the_template_leaves_every_slot_to_fill_and_names_no_source() -> None:
    """Frozen as it is, the template would tell nothing: its slots block, it holds no source."""
    text = TEMPLATE.read_text()
    verdict = notes.judge(_changes(), text)
    assert len(verdict.placeholders) >= 10
    assert notes.identifiers(text) == (set(), set(), [])
    assert [c.id for c in verdict.unhandled_features] == ["#10", "#13"]
    assert "Voor {{participants}}" in text


def _manuals() -> list[dict]:
    def task(screen: str, line: int, title: str) -> dict:
        return {"screen": screen, "line": line, "title": title}

    return [
        {"file": "00-aanpak", "role": "Projectleiding", "tasks": []},
        {"file": "10-beheerder", "role": "Beheerder", "tasks": []},
        {
            "file": "20-planner",
            "role": "Planner",
            "tasks": [task("group-agenda", 7, "De agenda lezen")],
        },
        {
            "file": "40-gasten",
            "role": "Gasten",
            "tasks": [task("participant:group-page", 3, "Een afspraak maken")],
        },
        {"file": DOCUMENT, "role": "Alle rollen", "tasks": []},
    ]


def test_the_tasks_of_the_manuals_tell_which_roles_a_screen_reaches() -> None:
    """The roles of the headings are the readers' manuals in file order, not the support ones."""
    places, participants = notes.topic_places(_manuals())
    assert places["group-agenda"] == [("Planner", "20-planner.md:7 › De agenda lezen")]
    assert participants == "Gasten"
    assert notes.heading_roles(_manuals()) == ["Beheerder", "Planner", "Gasten"]
    assert notes.topic_places([])[1] == "Deelnemers"


@pytest.fixture
def history(git: Git, tmp_path: Path) -> dict[str, str]:
    """Four commits on the main line after ``v0.6.0``, tagged ``v0.7.0``; hashes by number."""
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n- Soon.\n")
    commit_all(git, "feat(booking): the base (#1)")
    git("tag", "v0.6.0")
    hashes = {"1": git("rev-parse", "HEAD").strip()}
    steps = (
        ("10", "feat(planner): hold a pending booking (#10)", "docs/manual/nl/group-agenda.md"),
        (
            "11",
            "fix(booking): the date shows in the group's zone (#11)",
            "app/api/templates/book.html",
        ),
        ("12", "test(domain): more cases (#12)", "tests/test_x.py"),
        ("13", "feat(admin): a thing without a topic (#13)", "app/admin/routes.py"),
    )
    for number, subject, name in steps:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(subject)
        commit_all(git, subject)
        hashes[number] = git("rev-parse", "HEAD").strip()
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## Unreleased\n\n## 0.7.0 (2026-11-20)\n\n- Holding a booking.\n"
    )
    commit_all(git, "chore(release): 0.7.0 (#14)")
    hashes["14"] = git("rev-parse", "HEAD").strip()
    git("tag", "v0.7.0")
    return hashes


def test_the_changelog_of_a_release_or_else_its_unreleased_section(
    git: Git, history: dict[str, str]
) -> None:
    """A released section is found by the tag's version; a candidate falls back; none gives ''."""
    root = Path(git("rev-parse", "--show-toplevel").strip())
    released = release.changelog_for(root, history["14"], "v0.7.0")
    assert released.startswith("## 0.7.0 (2026-11-20)")
    assert "- Holding a booking." in released
    assert "## Unreleased" not in released
    assert release.changelog_for(root, history["1"], "v0.7.0-rc.2").endswith("- Soon.")
    assert release.changelog_for(root, "0" * 40, "v0.7.0") == ""


def _workspace(root: Path, history: dict[str, str], baseline: str = "1", end: str = "14") -> Path:
    """A workspace captured from ``end`` whose last frozen version is ``baseline``."""
    variables = {"product": "de dienst", "commit": history[baseline][:12]} if baseline else {}
    workspace = manuals_workspace(root, variables)
    (workspace / "build").mkdir()
    (workspace / "build" / "manuals.json").write_text(json.dumps({"manuals": _manuals()}))
    state = {"stack": {"build": {"commit": history[end]}}}
    (workspace / "evidence" / "state.json").write_text(json.dumps(state))
    return workspace


def test_the_notes_of_a_release_are_written_with_its_template(
    tmp_path_factory: pytest.TempPathFactory, git: Git, history: dict[str, str]
) -> None:
    """The range, the changes per role and the project's own text; the template when needed."""
    workspace = _workspace(tmp_path_factory.mktemp("workspace"), history)
    root = Path(git("rev-parse", "--show-toplevel").strip())
    done = release.notes(workspace, repo=root)
    assert (done.changes, done.visible) == (5, 3)
    assert done.document == workspace / "sources" / f"{DOCUMENT}.md"
    assert done.document.read_text() == TEMPLATE.read_text()
    text = done.markdown.read_text()
    assert "Van **0.6.0 (" in text
    assert "naar **0.7.0 (" in text
    assert "hold a pending booking (#10)" in text
    assert "Taken: 20-planner.md:7 › De agenda lezen" in text
    assert "- Holding a booking." in text
    data = json.loads((workspace / "evidence" / "release-notes.json").read_text())
    assert (data["from"], data["to"]) == (history["1"], history["14"])
    assert (data["from_release"], data["to_release"]) == ("v0.6.0", "v0.7.0")
    assert [c["id"] for c in data["changes"]] == ["#14", "#13", "#12", "#11", "#10"]


def test_an_existing_document_is_never_replaced_and_no_change_asks_for_none(
    tmp_path_factory: pytest.TempPathFactory, git: Git, history: dict[str, str]
) -> None:
    """A document a person wrote stays; a range with only technical changes asks for none."""
    root = Path(git("rev-parse", "--show-toplevel").strip())
    workspace = _workspace(tmp_path_factory.mktemp("kept"), history)
    (workspace / "sources").mkdir()
    (workspace / "sources" / f"{DOCUMENT}.md").write_text("# Mine\n")
    assert release.notes(workspace, repo=root).document is None
    assert (workspace / "sources" / f"{DOCUMENT}.md").read_text() == "# Mine\n"
    quiet = _workspace(tmp_path_factory.mktemp("quiet"), history, baseline="11", end="12")
    done = release.notes(quiet, repo=root)
    assert (done.changes, done.visible, done.document) == (1, 0, None)
    assert not (quiet / "sources").exists()


def test_a_first_version_has_no_notes(
    tmp_path_factory: pytest.TempPathFactory, git: Git, history: dict[str, str]
) -> None:
    """Without a baseline there is nothing to compare with, and nothing is written."""
    root = Path(git("rev-parse", "--show-toplevel").strip())
    workspace = _workspace(tmp_path_factory.mktemp("first"), history, baseline="")
    assert release.notes(workspace, repo=root) is None
    assert not (workspace / "evidence" / "release-notes.json").exists()


def test_a_capture_that_does_not_follow_the_baseline_is_refused(
    tmp_path_factory: pytest.TempPathFactory, git: Git, history: dict[str, str]
) -> None:
    """The baseline must be an ancestor of the capture: a range runs forwards."""
    root = Path(git("rev-parse", "--show-toplevel").strip())
    workspace = _workspace(tmp_path_factory.mktemp("back"), history, baseline="13", end="10")
    with pytest.raises(SystemExit, match="does not follow the baseline"):
        release.notes(workspace, repo=root)
    unknown = _workspace(tmp_path_factory.mktemp("unknown"), history)
    variables = json.loads((unknown / "variables.json").read_text()) | {"commit": "deadbeef"}
    (unknown / "variables.json").write_text(json.dumps(variables))
    with pytest.raises(SystemExit, match="deadbeef is no commit of this checkout"):
        release.notes(unknown, repo=root)


def _verdict(
    tmp_path: Path, sources: dict[str, str], changes: list, *, from_: str = "a" * 40
) -> tuple[Any, dict[str, str]]:
    """Run the release-notes check on a workspace with ``sources`` and the given ``changes``."""
    root = manuals_workspace(tmp_path, {})
    (root / "sources").mkdir()
    paths = {}
    for stem, text in sources.items():
        (root / "sources" / f"{stem}.md").write_text(text)
        paths[root / "sources" / f"{stem}.md"] = text
    result = notes.Result(from_, "b" * 40, "", "", tuple(changes))
    (root / "evidence" / "release-notes.json").write_text(result.as_json())
    report, hashes = check.Report(), {}
    check.check_release_notes(check.Workspace(root), paths, "a" * 12, hashes, report)
    return report, hashes


def test_the_check_blocks_what_the_document_leaves_open(tmp_path: Path) -> None:
    """The template, an unknown source and a feature left out block; a fix is only listed."""
    report, hashes = _verdict(
        tmp_path, {DOCUMENT: TEMPLATE.read_text() + "<!-- bron: #99 -->\n"}, _changes()
    )
    lines = report.sections["Releasenotes"]
    assert report.blocking == len([line for line in lines if line.startswith("**Blokkerend:**")])
    joined = "\n".join(lines)
    assert "nog niet ingevuld" in joined
    assert "`#99` is geen wijziging van deze release" in joined
    assert "nieuwe functie #10 (planner: hold)" in joined
    assert "nog niet genoemd of weggelaten: #11 fix" in joined
    assert "**Blokkerend:** nog niet genoemd" not in joined
    assert hashes["evidence/release-notes.json"]


def test_the_check_passes_a_document_that_names_each_change(tmp_path: Path) -> None:
    """Nothing blocks when every feature is named or left out and no slot remains."""
    report, _ = _verdict(tmp_path, {DOCUMENT: FILLED}, _changes())
    assert report.blocking == 0
    assert report.sections["Releasenotes"][0].startswith("4 wijzigingen sinds aaaaaaaaa, 3 die")


def test_the_check_asks_for_the_document_only_when_a_reader_can_notice_a_change(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """No document is a finding when features or fixes changed, and none when only tests did."""
    report, _ = _verdict(tmp_path_factory.mktemp("a"), {}, _changes())
    assert report.blocking == 1
    quiet = [c for c in _changes() if not c.visible]
    report, _ = _verdict(tmp_path_factory.mktemp("b"), {}, quiet)
    assert report.blocking == 0


def test_the_check_refuses_notes_of_another_range_or_none(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Notes that begin elsewhere than the baseline, or are missing, block."""
    report, _ = _verdict(
        tmp_path_factory.mktemp("other"), {DOCUMENT: FILLED}, _changes(), from_="c" * 40
    )
    assert report.blocking == 1
    assert "beginnen bij ccccccccc, niet bij aaaaaaaaa" in report.sections["Releasenotes"][0]
    root = manuals_workspace(tmp_path_factory.mktemp("none"), {})
    report = check.Report()
    check.check_release_notes(check.Workspace(root), {}, "a" * 12, {}, report)
    assert report.blocking == 1
    assert "ontbreekt; draai `release.py notes`" in report.sections["Releasenotes"][0]


def test_the_release_notes_have_no_role_of_their_own() -> None:
    """Like the approach, they have no tasks and no rights; their labels are still checked."""
    document = {"file": DOCUMENT, "role": "Alle rollen", "tasks": [], "figures": [], "bold": []}
    approach = {
        "file": "00-aanpak",
        "role": "Projectleiding",
        "tasks": [],
        "figures": [],
        "bold": [],
    }
    rights = check.Rights(roles={}, matrix={}, participant=frozenset({"participant.book"}))
    assert rights.of(document) == rights.of(approach) == frozenset()
    report = check.Report()
    ctx = {
        "rights": rights,
        "codes": set(),
        "topics": set(),
        "shots": set(),
        "used": set(),
        "tasks": 0,
    }
    check.check_tasks(document, ctx, report)
    assert report.sections == {}


def test_the_overview_lists_every_frozen_version_newest_first(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A version from before the notes existed is listed too, by its folder name."""
    workspace = manuals_workspace(tmp_path_factory.mktemp("versions"), {})
    versions = workspace / "versies"
    old = versions / "2026-11-08_software-0.6.0-rc.35_handleiding-1.0"
    new = versions / "2026-12-01_software-0.7.0_handleiding-2.0"
    for folder in (old, new):
        folder.mkdir(parents=True)
    (old / "LEESMIJ.md").write_text("- **Vastgesteld:** 9 november 2026.\n")
    (new / "LEESMIJ.md").write_text("- **Bevroren:** 2 december 2026.\n")
    (new / "evidence").mkdir()
    (new / "evidence" / "release-notes.json").write_text(
        notes.Result("a" * 40, "b" * 40, "", "", tuple(_changes()[:3])).as_json()
    )
    (versions / f"{new.name}.zip.sha256").write_text(f"{'ab' * 32}  {new.name}.zip\n")
    (versions / "notes.txt").write_text("not a version")
    path = release.overview(workspace)
    rows = [
        line
        for line in path.read_text().splitlines()
        if line.startswith("| ") and "---" not in line
    ][1:]
    assert rows == [
        "| 2.0 | 0.7.0 | 1 december 2026 | 2 december 2026 | 3 wijzigingen | abababababab "
        "| `2026-12-01_software-0.7.0_handleiding-2.0/` |",
        "| 1.0 | 0.6.0-rc.35 | 8 november 2026 | 9 november 2026 | – | – "
        "| `2026-11-08_software-0.6.0-rc.35_handleiding-1.0/` |",
    ]
    before = path.stat().st_mtime_ns
    assert release.overview(workspace) == path
    assert path.stat().st_mtime_ns == before


def test_a_docs_change_to_a_help_topic_is_a_change_readers_can_notice() -> None:
    """Readers see a help topic behind the (i) of a screen; a docs commit on it counts."""
    changes = notes.classify(
        [
            _commit(
                "a", "docs(manual): explain the agenda (#20)", "docs/manual/nl/group-agenda.md"
            ),
            _commit("b", "docs(adr): a decision (#21)", "docs/adr/0001-x.md"),
        ],
        PLACES,
        "Deelnemers",
    )
    assert [(c.id, c.visible, c.roles) for c in changes] == [
        ("#20", True, ("Planner",)),
        ("#21", False, ()),
    ]


def test_the_result_is_written_and_read_by_one_class() -> None:
    """The file round-trips, and answers where it begins and which range it covers."""
    result = notes.Result("a" * 40, "b" * 40, "v1.0.0", "v1.1.0", tuple(_changes()))
    again = notes.Result.from_json(result.as_json())
    assert again == result
    assert again.begins_at("a" * 12)
    assert not again.begins_at("")
    assert not again.begins_at("b" * 12)
    assert again.covers("a" * 40, "b" * 40)
    assert not again.covers("a" * 40, "c" * 40)
    with pytest.raises(KeyError):
        notes.Result.from_json("{}")


def test_a_baseline_is_a_hash_and_a_checkout_must_descend_from_it(
    tmp_path_factory: pytest.TempPathFactory, git: Git, history: dict[str, str]
) -> None:
    """An option or a ref is refused; a baseline that is no ancestor stops before a capture."""
    root = Path(git("rev-parse", "--show-toplevel").strip())
    assert release.baseline_of({}) == ""
    assert release.baseline_of({"commit": "b9e01b01c9de"}) == "b9e01b01c9de"
    for bad in ("--output=x", "HEAD~1", "v0.6.0"):
        with pytest.raises(SystemExit, match="is no commit hash"):
            release.baseline_of({"commit": bad})
    ahead = history["14"][:12]
    workspace = manuals_workspace(tmp_path_factory.mktemp("ahead"), {"commit": ahead})
    git("switch", "-q", "--detach", history["1"])
    with pytest.raises(SystemExit, match="does not follow the baseline"):
        release.check_baseline(workspace, repo=root)
    git("switch", "-q", "--detach", history["14"])
    release.check_baseline(workspace, repo=root)
    release.check_baseline(manuals_workspace(tmp_path_factory.mktemp("first"), {}), repo=root)


def test_a_released_changelog_section_never_falls_back_to_unreleased(
    git: Git, history: dict[str, str]
) -> None:
    """Only a candidate or an untagged commit may quote *Unreleased*; a release has its own."""
    root = Path(git("rev-parse", "--show-toplevel").strip())
    assert release.changelog_for(root, history["1"], "v0.7.0") == ""
    assert release.changelog_for(root, history["1"], "").endswith("- Soon.")


def test_a_malformed_notes_file_is_a_finding_not_a_traceback(tmp_path: Path) -> None:
    """check.py reports it as blocking, so the freeze stops with a message."""
    root = manuals_workspace(tmp_path, {})
    (root / "evidence" / "release-notes.json").write_text('{"from": "a"}')
    report = check.Report()
    check.check_release_notes(check.Workspace(root), {}, "a" * 12, {}, report)
    assert report.blocking == 1
    assert "niet te lezen" in report.sections["Releasenotes"][0]
