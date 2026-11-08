"""The pure parts of the role-manuals check (``.claude/skills/role-manuals/scripts/check.py``).

The script runs on an organisation's workspace, outside CI; these tests keep its reading of
labels, its rename and its matrix mapping honest.
"""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / ".claude/skills/role-manuals/scripts/check.py"
_spec = importlib.util.spec_from_file_location("role_manuals_check", SCRIPT)
assert _spec is not None
assert _spec.loader is not None
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def test_labels_are_read_per_paragraph_and_path_parts_split() -> None:
    """A bold span that wraps is one label; a path gives its parts; structure words are not."""
    text = "Kies **Om nieuwe\ntijd vragen**.\n\n**Tip:** ga naar **Planning → Bezetting**.\n"
    found = [
        label for _, paragraph in check.paragraphs(text) for label in check.labels_in(paragraph)
    ]
    assert found == ["Om nieuwe tijd vragen", "Planning", "Bezetting"]


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


def test_only_a_commit_hash_is_passed_to_git() -> None:
    """A value that could be read as an option never reaches git."""
    assert check.COMMIT.fullmatch("d36485a6ee78")
    assert not check.COMMIT.fullmatch("--output=/tmp/x")
    assert not check.COMMIT.fullmatch("HEAD~1")
