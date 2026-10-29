"""The next release number from the Conventional Commits since the last tag (ADR 0051)."""

from pathlib import Path

import pytest

from scripts.release_plan import next_version, project_version


@pytest.mark.parametrize(
    ("current", "subjects", "expected"),
    [
        # Before 1.0: a feature or a breaking change is a minor release, fixes a patch.
        ("0.2.0", ["fix(admin): label", "docs: runbook"], "0.2.1"),
        ("0.2.0", ["security: patch go-jose", "chore(deps): bump httpx"], "0.2.1"),
        ("0.2.1", ["fix: a", "feat(queue): own queue"], "0.3.0"),
        ("0.2.0", ["feat(api)!: drop v0 field"], "0.3.0"),
        # From 1.0: a breaking change is a major release.
        ("1.4.2", ["feat(api)!: drop field"], "2.0.0"),
        ("1.4.2", ["fix: x\n\nBREAKING CHANGE: env FOO renamed to BAR"], "2.0.0"),
        ("1.4.2", ["feat: y"], "1.5.0"),
        ("1.4.2", ["fix: z"], "1.4.3"),
    ],
)
def test_next_version_follows_the_commit_types(
    current: str, subjects: list[str], expected: str
) -> None:
    assert next_version(current, subjects) == expected


def test_only_documentation_or_tests_is_still_a_patch() -> None:
    # A release is a deliberate step; whatever is on main ships, at least as a patch.
    assert next_version("0.2.0", ["docs: adr", "test(e2e): midnight"]) == "0.2.1"


def test_nothing_since_the_last_tag_is_no_release() -> None:
    assert next_version("0.2.0", []) is None


def test_project_version_reads_pyproject(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nname = "x"\nversion = "0.3.0"\n')
    assert project_version(path) == "0.3.0"
