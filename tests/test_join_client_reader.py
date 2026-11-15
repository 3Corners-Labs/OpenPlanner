"""A service's client is read in one place (ADR 0123 step 6a).

``app.domain.call_client`` answers which client a booking's participant gets, whether they get
the call page, how a redirect to the video platform is shaped, and what was chosen. A route, a
job, an adapter or a template that read the group's ``join_client`` for itself would decide that
a second time (and a third), which is how two places once disagreed about ``embedded``.

The scan looks for the word, not only for ``group.join_client``: an attribute, a name, a keyword
argument, a string constant (``getattr(group, "join_client")``, ``group["join_client"]``) in any
file of ``app/``, and the bare word in a template (``group|attr('join_client')``). Docstrings and
comments are left out. A file that holds the word must be on the list below with the reason why
it makes no decision about the client; a listed file that no longer holds it fails too.
"""

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
WORD = re.compile(r"\bjoin_client\b")

HOLDERS = {
    "app/domain/call_client.py": "the one reader: every decision about the client is made here",
    "app/domain/scheduling.py": "declares the field and its default on the group",
    "app/domain/planning.py": (
        "declares the field of GroupSettings; the copy of a group into its settings walks every"
        " field with getattr and decides nothing"
    ),
    "app/adapters/db/models.py": "the column and the CHECK of the allowed values",
    "app/adapters/db/uow.py": "the persistence adapter reads and writes the column",
    "app/adapters/db/config_group.py": "the column list of the configuration package's export",
    "app/adapters/db/seed.py": "creates the demo team that chose the page; sets the column",
    "app/domain/configuration_v1_group.py": (
        "the configuration package: field spec, enum and the refusal of a new ``embedded``"
    ),
    "app/admin/call_client_input.py": "the settings form's own select and its refusals",
    "app/admin/group_routes.py": "hands the form's choice to GroupSettings, no decision",
    "app/admin/forms.py": "the message a refused field shows, by field name",
    "app/admin/audit_routes.py": "the label of a changed field in the audit view, by field name",
}
"""The files of ``app/`` that may hold the word, each with why it decides nothing about the
client (or is the reader)."""


def _docstrings(tree: ast.AST) -> set[int]:
    """Return the ids of string constants that stand alone as a statement (docstrings)."""
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def _holds_word(tree: ast.AST) -> bool:
    """Return whether the code (not a docstring) uses ``join_client`` in any form."""
    skip = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "join_client":
            return True
        if isinstance(node, ast.Name) and node.id == "join_client":
            return True
        if isinstance(node, ast.keyword) and node.arg == "join_client":
            return True
        if isinstance(node, ast.arg) and node.arg == "join_client":
            return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
            and WORD.search(node.value)
        ):
            return True
    return False


def _found() -> set[str]:
    """Return every file of ``app/`` (code and templates) that holds the word."""
    found = set()
    for path in sorted(APP.rglob("*.py")):
        if _holds_word(ast.parse(path.read_text(), filename=str(path))):
            found.add(path.relative_to(APP.parent).as_posix())
    for path in sorted(APP.rglob("*.html")):
        if WORD.search(path.read_text()):
            found.add(path.relative_to(APP.parent).as_posix())
    return found


def test_no_route_job_adapter_or_template_holds_the_clients_field_name_unlisted() -> None:
    assert _found() - set(HOLDERS) == set()  # a template gets a value its route asked for


def test_every_listed_file_still_holds_it() -> None:
    """The list stays honest: a file that no longer holds the word leaves it."""
    assert set(HOLDERS) - _found() == set()


def test_the_scan_sees_every_way_of_reading_the_field() -> None:
    for source in (
        "group.join_client",
        'getattr(group, "join_client")',
        'group["join_client"]',
        'make(join_client="x")',
        "join_client = 1",
    ):
        assert _holds_word(ast.parse(source)), source
    assert not _holds_word(ast.parse('"""About join_client."""\nx = 1'))
    assert WORD.search("{{ group|attr('join_client') }}")
