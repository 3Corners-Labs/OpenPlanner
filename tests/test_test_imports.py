"""No module under ``tests/`` imports from a test module.

Importing a test module brings its fixtures and tests along and couples two tests that should
run on their own. What several tests share has a home: fakes in ``tests/fakes/``, constants,
builders and harnesses in ``tests/support/``, fixtures in the nearest ``conftest.py``. The
source is read, not imported, as ``tests/test_fakes.py`` does.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


def _module(path: Path) -> str:
    """Return the dotted name of the module at ``path``."""
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def _is_test_module(name: str) -> bool:
    """Return whether the dotted ``name`` is a test module or something inside one."""
    return any(part.startswith("test_") for part in name.split("."))


def _imported(source: str, module: str) -> list[tuple[int, str]]:
    """Return each name that the source of ``module`` imports, with its line, wherever it stands.

    A relative import is resolved against the package of ``module``; ``from package import name``
    also yields ``package.name``, because ``name`` may be a module.
    """
    package = module.split(".")[:-1]
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            imported = ".".join([*base, *([node.module] if node.module else [])])
            found.append((node.lineno, imported))
            found += [(node.lineno, f"{imported}.{alias.name}") for alias in node.names]
    return found


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
        ]
    )

    found = {name for _, name in _imported(source, "tests.api.test_g") if _is_test_module(name)}

    assert {f"tests.api.test_{letter}" for letter in "abcdef"} <= found


def test_no_module_under_tests_imports_a_test_module() -> None:
    importing: dict[str, str] = {}
    for path in sorted(TESTS.rglob("*.py")):
        for line, name in _imported(path.read_text(encoding="utf-8"), _module(path)):
            if name.startswith("tests.") and _is_test_module(name):
                importing.setdefault(f"{path.relative_to(ROOT)}:{line}", name)

    assert importing == {}, "move what is shared to tests/support/, tests/fakes/ or a conftest.py"
