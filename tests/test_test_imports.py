"""No module under ``tests/`` imports from a test module or from a ``conftest.py``.

Importing a test module brings its fixtures and tests along and couples two tests that should
run on their own. A ``conftest.py`` is pytest's to load: importing one as a module as well
gives its fixtures a second way in and lets helpers hide among them. What several tests share
has a home: fakes in ``tests/fakes/``, constants, builders and harnesses in ``tests/support/``,
fixtures in the nearest ``conftest.py``, which pytest hands to the tests below it. The source
is read, not imported, as ``tests/test_fakes.py`` does.
"""

import ast
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


def _module(path: Path) -> str:
    """Return the dotted name of the module at ``path``."""
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def _is_test_module(name: str) -> bool:
    """Return whether the dotted ``name`` is a test module or something inside one."""
    return any(part.startswith("test_") for part in name.split("."))


def _is_conftest(name: str) -> bool:
    """Return whether the dotted ``name`` is a ``conftest`` module or something inside one."""
    return "conftest" in name.split(".")


def _plugins(node: ast.AST) -> list[tuple[int, str]]:
    """Return the modules a ``pytest_plugins`` assignment names, with its line; else nothing."""
    if not isinstance(node, ast.Assign | ast.AnnAssign) or node.value is None:
        return []
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    if not any(
        isinstance(target, ast.Name) and target.id == "pytest_plugins" for target in targets
    ):
        return []
    values = node.value.elts if isinstance(node.value, ast.List | ast.Tuple) else [node.value]
    return [
        (node.lineno, v.value)
        for v in values
        if isinstance(v, ast.Constant) and isinstance(v.value, str)
    ]


def _imported(source: str, module: str) -> list[tuple[int, str]]:
    """Return each name that the source of ``module`` imports, with its line, wherever it stands.

    A relative import is resolved against the package of ``module``; ``from package import name``
    also yields ``package.name``, because ``name`` may be a module. A module named in
    ``pytest_plugins`` counts too: pytest imports it, and its fixtures reach every test.
    """
    package = module.split(".")[:-1]
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        found += _plugins(node)
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            imported = ".".join([*base, *([node.module] if node.module else [])])
            found.append((node.lineno, imported))
            found += [(node.lineno, f"{imported}.{alias.name}") for alias in node.names]
    return found


def _importing(forbidden: Callable[[str], bool]) -> dict[str, str]:
    """Return, per file and line under ``tests/``, the first ``tests.`` name it may not import."""
    importing: dict[str, str] = {}
    for path in sorted(TESTS.rglob("*.py")):
        for line, name in _imported(path.read_text(encoding="utf-8"), _module(path)):
            if name.startswith("tests.") and forbidden(name):
                importing.setdefault(f"{path.relative_to(ROOT)}:{line}", name)
    return importing


def test_every_form_of_import_is_found() -> None:
    source = "\n".join(
        [
            "import tests.api.test_a",
            "from tests.api.test_b import helper",
            "from tests.api import test_c",
            "from .test_d import helper",
            "from . import test_e",
            "def later():",
            "    from tests.api.test_f import helper",
            'pytest_plugins = ["tests.api.test_h"]',
        ]
    )

    found = {name for _, name in _imported(source, "tests.api.test_g") if _is_test_module(name)}

    assert {f"tests.api.test_{letter}" for letter in "abcdefh"} <= found


def test_every_form_of_conftest_import_is_found() -> None:
    source = "\n".join(
        [
            "import tests.conftest",
            "from tests.admin.conftest import Site",
            "from tests.web import conftest",
            "from .conftest import helper",
            "from ..domain import conftest",
            "def later():",
            "    from tests.scripts.conftest import helper",
            'pytest_plugins = ["tests.jobs.conftest"]',
        ]
    )

    found = {name for _, name in _imported(source, "tests.api.test_g") if _is_conftest(name)}

    assert {
        f"tests.{package}conftest"
        for package in ("", "admin.", "web.", "api.", "domain.", "scripts.", "jobs.")
    } <= found
    assert not _is_conftest("tests.support.conftest_free")


def test_no_module_under_tests_imports_a_test_module() -> None:
    importing = _importing(_is_test_module)

    assert importing == {}, "move what is shared to tests/support/, tests/fakes/ or a conftest.py"


def test_no_module_under_tests_imports_a_conftest() -> None:
    importing = _importing(_is_conftest)

    assert importing == {}, (
        "move what is shared to tests/support/ or tests/fakes/; "
        "a fixture reaches the tests below its conftest.py without an import"
    )
