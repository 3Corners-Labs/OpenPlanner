"""The next release number from the Conventional Commits since the last tag (ADR 0052)."""

from pathlib import Path

import pytest

from scripts.release_plan import (
    candidate_base,
    candidate_version,
    next_version,
    project_version,
)


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


# Release candidates (ADR 0059): one per merge to main, numbered within the next release.


def test_candidate_follows_the_rules_unless_the_release_pr_already_raised_pyproject() -> None:
    assert candidate_base("0.3.0", "0.3.0", ["feat: a"]) == "0.4.0"
    assert candidate_base("0.3.0", "0.3.0", ["fix: a"]) == "0.3.1"
    # The release PR for 0.4.0 is merged, with only fixes since 0.3.0: candidates are of 0.4.0.
    assert candidate_base("0.3.0", "0.4.0", ["fix: a", "chore(release): v0.4.0"]) == "0.4.0"
    # pyproject still says the released version: it does not pull the number down.
    assert candidate_base("0.3.0", "0.2.0", ["feat: a"]) == "0.4.0"


def test_no_candidate_without_commits_since_the_release() -> None:
    assert candidate_base("0.3.0", "0.3.0", []) is None


def test_candidate_number_is_one_above_the_highest_existing_one() -> None:
    assert candidate_version("0.4.0", []) == "0.4.0-rc.1"
    assert candidate_version("0.4.0", ["v0.4.0-rc.1", "v0.4.0-rc.2"]) == "0.4.0-rc.3"
    # Lexical order would put rc.9 above rc.10; numbers decide.
    assert candidate_version("0.4.0", ["v0.4.0-rc.9", "v0.4.0-rc.10"]) == "0.4.0-rc.11"
    # Other versions' candidates and stray tags do not count.
    assert candidate_version("0.4.0", ["v0.3.0-rc.7", "v0.4.0-rc1", "v0.4.0"]) == "0.4.0-rc.1"
