"""Keep the most coupled symbols of ``app/`` from growing unnoticed (ADR 0127 step 3).

The Graphify review of every pull request lists the symbols that many others depend on
(``Settings``, ``Transaction``) or that depend on many others (``create_app``, ``run_worker``). It
is advisory, and the service describes an older commit than the one under review. This script holds
the same idea where CI can fail on it: a small list of symbols, each with a ceiling, measured on
the checked-out code with the standard library.

Two measures, each deliberately simple:

- ``afferent``: how many files of ``app/`` other than the defining one name the symbol, as an
  import, a name or an attribute. This counts files, not call sites, so a file that uses the
  symbol twenty times counts once.
- ``efferent``: how many different names the symbol's own body calls.

The numbers differ from Graphify's (which counts functions and resolves calls); what matters is
the direction. A ceiling is raised by editing ``scripts/coupling_budget.json`` in the same pull
request, with a ``reason``, so the growth is a decision a reviewer sees. A measure far under its
ceiling is reported so the ceiling can be lowered; that never fails the run.

    python3 -m scripts.coupling_budget [--root PATH]

Exit 1 when a measure is over its ceiling or a listed symbol cannot be found. Standard library
only.
"""

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

BUDGET_FILE = Path(__file__).with_name("coupling_budget.json")
SLACK = 0.10
KINDS = ("afferent", "efferent")


@dataclass(frozen=True)
class Entry:
    """One symbol of the budget: where it is, what is measured and the ceiling."""

    symbol: str
    path: str
    kind: str
    ceiling: int
    reason: str


def load(path: Path = BUDGET_FILE) -> list[Entry]:
    """Read the budget file.

    Args:
        path: The JSON file listing the entries.

    Returns:
        The entries, in file order.

    Raises:
        ValueError: An entry has an unknown kind, a non-positive ceiling or no reason.
    """
    entries = []
    for raw in json.loads(path.read_text(encoding="utf-8"))["symbols"]:
        entry = Entry(raw["symbol"], raw["path"], raw["kind"], int(raw["ceiling"]), raw["reason"])
        if entry.kind not in KINDS or entry.ceiling < 1 or not entry.reason.strip():
            raise ValueError(f"bad budget entry for {entry.symbol}")
        entries.append(entry)
    return entries


def _names(tree: ast.AST) -> set[str]:
    """Return every identifier a tree imports, reads, assigns or accesses as an attribute."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.ImportFrom | ast.Import):
            found.update(alias.name.split(".")[-1] for alias in node.names)
    return found


def _definition(root: Path, entry: Entry) -> ast.AST:
    """Return the definition of the symbol in its file.

    Raises:
        LookupError: The file is missing or does not define the symbol, so a renamed or deleted
            symbol fails the check instead of measuring as zero.
    """
    try:
        tree = ast.parse((root / entry.path).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise LookupError(entry.symbol) from error
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and (
            node.name == entry.symbol
        ):
            return node
    raise LookupError(entry.symbol)


def afferent(root: Path, entry: Entry) -> int:
    """Count the files of ``app/`` besides the defining one that name the symbol.

    Raises:
        LookupError: The symbol is not defined in its file.
    """
    _definition(root, entry)
    own = (root / entry.path).resolve()
    count = 0
    for file in sorted((root / "app").rglob("*.py")):
        if file.resolve() == own:
            continue
        tree = ast.parse(file.read_text(encoding="utf-8"))
        if entry.symbol in _names(tree):
            count += 1
    return count


def efferent(root: Path, entry: Entry) -> int:
    """Count the different names that the body of the symbol calls.

    Raises:
        LookupError: The symbol is not defined in its file.
    """
    node = _definition(root, entry)
    called: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call):
            func = inner.func
            called.add(func.id if isinstance(func, ast.Name) else getattr(func, "attr", ""))
    called.discard("")
    return len(called)


def measure(root: Path, entry: Entry) -> int:
    """Return the current value of the entry's measure."""
    return afferent(root, entry) if entry.kind == "afferent" else efferent(root, entry)


def report(root: Path, entries: list[Entry]) -> tuple[list[str], list[str]]:
    """Measure every entry and sort the result into failures and hints.

    Args:
        root: The repository root.
        entries: The budget.

    Returns:
        The failures (over a ceiling, or a symbol not found) and the hints (a ceiling that could
        be lowered).
    """
    failures: list[str] = []
    hints: list[str] = []
    for entry in entries:
        try:
            value = measure(root, entry)
        except LookupError:
            failures.append(f"{entry.symbol}: not found in {entry.path}")
            continue
        if value > entry.ceiling:
            failures.append(
                f"{entry.symbol} ({entry.kind}): {value} is over its ceiling {entry.ceiling}"
            )
        elif value < entry.ceiling * (1 - SLACK):
            hints.append(
                f"{entry.symbol} ({entry.kind}): {value}, ceiling {entry.ceiling} can be lowered"
            )
    return failures, hints


def main(argv: list[str] | None = None) -> int:
    """Run the check on the repository and print what it found."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    try:
        entries = load(BUDGET_FILE)
    except (ValueError, KeyError, OSError) as error:
        print(f"coupling: the budget file is unusable: {error}", file=sys.stderr)
        return 1
    failures, hints = report(args.root, entries)
    for line in hints:
        print(f"hint: {line}")
    for line in failures:
        print(f"coupling: {line}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
