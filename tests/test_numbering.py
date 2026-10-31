"""Two ADRs or two migrations never share a number, and the migration chain has one head.

Parallel branches claim the same number without noticing each other (four times on
2026-10-30). Each branch passes on its own; the merge queue runs these tests on ``main`` plus
the queued pull requests, so the second one is sent back with the collision named (ADR 0078
point 5).
"""

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADRS = sorted(ROOT.glob("docs/adr/[0-9][0-9][0-9][0-9]-*.md"))
MIGRATIONS = sorted(ROOT.glob("migrations/versions/[0-9][0-9][0-9][0-9]_*.py"))

_REVISION = re.compile(r"^revision(?::\s*str)?\s*=\s*\"(\w+)\"", re.M)
_DOWN = re.compile(r"^down_revision(?::[^=]+)?\s*=\s*(.+)$", re.M)
_QUOTED = re.compile(r"\"(\w+)\"")


def _duplicates(numbers: list[str]) -> list[str]:
    return sorted(number for number, count in Counter(numbers).items() if count > 1)


def test_adr_numbers_are_unique() -> None:
    assert ADRS
    assert _duplicates([adr.name[:4] for adr in ADRS]) == []


def test_migration_numbers_are_unique() -> None:
    assert MIGRATIONS
    assert _duplicates([migration.name[:4] for migration in MIGRATIONS]) == []


def test_each_migration_file_number_is_its_revision() -> None:
    mismatched = {}
    for migration in MIGRATIONS:
        found = _REVISION.search(migration.read_text())
        assert found, f"{migration.name} declares no revision"
        if found.group(1) != migration.name[:4]:
            mismatched[migration.name] = found.group(1)

    assert mismatched == {}


def test_the_migration_chain_has_one_head() -> None:
    revisions = {migration.name[:4] for migration in MIGRATIONS}
    downs = {}
    for migration in MIGRATIONS:
        found = _DOWN.search(migration.read_text())
        assert found, f"{migration.name} declares no down_revision"
        # A merge migration lists two parents in a tuple; the first migration says ``None``.
        downs[migration.name[:4]] = set(_QUOTED.findall(found.group(1)))

    unknown = {rev: sorted(d - revisions) for rev, d in downs.items() if d - revisions}
    heads = sorted(revisions - set().union(*downs.values()))

    assert unknown == {}, "a migration points at a revision that does not exist"
    assert len(heads) == 1, f"more than one head: {heads}"
