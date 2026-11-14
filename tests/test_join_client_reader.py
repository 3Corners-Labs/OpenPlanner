"""A service's client is read in one place (ADR 0123 step 6a).

``app.domain.call_client`` answers which client a booking's participant gets, whether they get
the call page, how a redirect to the video platform is shaped, and what was chosen. A route, a
job, an adapter or a template that read the group's ``join_client`` for itself would decide that
a second time (and a third), which is how two places once disagreed about ``embedded``. So no
file of ``app/`` outside the few that persist, edit or carry the field reads it.
"""

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

READERS = frozenset(
    {
        "app/domain/call_client.py",  # the one reader
        "app/adapters/db/uow.py",  # persists the group
        "app/admin/call_client_input.py",  # the form edits the group's own setting
        "app/domain/configuration_v1_group.py",  # the configuration package carries it
    }
)
"""The files that may read ``<something>.join_client``."""

TEMPLATE_READ = re.compile(r"\.join_client\b")


def _python_reads() -> set[str]:
    """Return the files of ``app/`` that read an attribute called ``join_client``."""
    found = set()
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        if any(isinstance(n, ast.Attribute) and n.attr == "join_client" for n in ast.walk(tree)):
            found.add(path.relative_to(APP.parent).as_posix())
    return found


def _template_reads() -> set[str]:
    """Return the templates of ``app/`` that read ``join_client`` of anything."""
    return {
        path.relative_to(APP.parent).as_posix()
        for path in sorted(APP.rglob("*.html"))
        if TEMPLATE_READ.search(path.read_text())
    }


def test_no_route_job_adapter_or_template_reads_the_clients_field_itself() -> None:
    assert _python_reads() - READERS == set()
    assert _template_reads() == set()  # a template gets a value its route asked the module for


def test_every_file_that_may_read_it_does() -> None:
    """The list stays honest: a file that no longer reads the field leaves it."""
    assert READERS - _python_reads() == set()
