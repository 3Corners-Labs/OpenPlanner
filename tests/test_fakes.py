"""Every port of ``app/domain/ports.py`` has its fake in ``tests/fakes/`` and nowhere else.

CLAUDE.md: tests use fakes of the ports, and the fakes live in ``tests/fakes/``. A class fakes a
port when it defines every method of the port with the same parameter names. The source is
read, not imported, so no fake or test module runs.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
FAKES = TESTS / "fakes"

INCOMPLETE = frozenset({"Transaction"})
"""Ports whose fake holds only what the domain tests use; one shared method makes it the fake.

The in-memory transaction leaves out what only the SQLite adapter's own tests need.
"""

Methods = dict[str, tuple[str, ...]]


def _classes(path: Path) -> list[ast.ClassDef]:
    """Return every class defined in ``path``, nested ones too."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]


def _methods(node: ast.ClassDef) -> Methods:
    """Return the public methods ``node`` defines, each with its parameter names after ``self``."""
    return {
        item.name: tuple(
            arg.arg for arg in [*item.args.posonlyargs, *item.args.args, *item.args.kwonlyargs][1:]
        )
        for item in node.body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
        and not item.name.startswith("_")
    }


def _fakes(methods: Methods, port: Methods, *, incomplete: bool = False) -> bool:
    """Return whether a class with ``methods`` fakes ``port`` (one method will do if incomplete)."""
    shared = [name for name, parameters in port.items() if methods.get(name) == parameters]
    return bool(shared) if incomplete else len(shared) == len(port)


PORTS = {
    node.name: _methods(node)
    for node in _classes(ROOT / "app" / "domain" / "ports.py")
    if any(isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases)
}


def test_the_ports_are_found() -> None:
    assert {"Clock", "EventSink", "RequestLog", "Transaction", "UnitOfWork"} <= set(PORTS)
    assert INCOMPLETE <= set(PORTS)
    assert all(PORTS.values())


def test_every_port_has_a_fake_in_tests_fakes() -> None:
    fakes = [_methods(node) for path in sorted(FAKES.glob("*.py")) for node in _classes(path)]

    unfaked = [
        port
        for port, members in PORTS.items()
        if not any(_fakes(methods, members, incomplete=port in INCOMPLETE) for methods in fakes)
    ]

    assert unfaked == []


def test_no_test_keeps_a_fake_of_a_port_beside_it() -> None:
    beside = [
        f"{path.relative_to(ROOT)}:{node.lineno} {node.name} fakes {port}"
        for path in sorted(TESTS.rglob("*.py"))
        if FAKES not in path.parents
        for node in _classes(path)
        for port, members in PORTS.items()
        if _fakes(_methods(node), members)
    ]

    assert beside == []
