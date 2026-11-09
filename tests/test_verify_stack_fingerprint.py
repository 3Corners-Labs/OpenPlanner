"""``stack.sh fingerprint`` of the verify-openplanner skill: the files an image is built from.

role-manuals' ``release.py`` compares the fingerprint of a run with that of its release tag to
say whether the manuals show the tag's software. So equal hashes must mean the same image files,
a change to one of them (committed or not) another hash, and a commit's hash must not depend on
the checkout it is taken from. Runs on a throw-away repository.
"""

from pathlib import Path

import pytest

from tests.support.git_repository import Git, commit_all
from tests.support.skill_scripts import fingerprint, image_checkout


@pytest.fixture
def checkout(git: Git, tmp_path: Path) -> Path:
    """The script in a repository whose image takes ``app/`` only, tagged ``v1``."""
    image_checkout(git, tmp_path)
    git("tag", "v1")
    return tmp_path


def test_a_commit_that_changes_no_image_file_keeps_the_tags_fingerprint(
    git: Git, checkout: Path
) -> None:
    """A later commit outside the image files, and a clean checkout of it, hash as the tag."""
    (checkout / "notes.md").write_text("two\n")
    commit_all(git, "notes")
    tagged = fingerprint(checkout, "v1").stdout
    assert len(tagged.strip()) == 64
    assert fingerprint(checkout, "HEAD").stdout == tagged
    assert fingerprint(checkout).stdout == tagged


def test_a_changed_image_file_changes_it_committed_or_not(git: Git, checkout: Path) -> None:
    """A local edit already differs from the tag, and so does the commit that holds it."""
    tagged = fingerprint(checkout, "v1").stdout
    (checkout / "app" / "main.py").write_text("VALUE = 2\n")
    assert fingerprint(checkout).stdout != tagged
    assert fingerprint(checkout, "v1").stdout == tagged, "a commit's hash ignores local edits"
    commit_all(git, "app")
    assert fingerprint(checkout, "HEAD").stdout == fingerprint(checkout).stdout != tagged


def test_a_commit_is_hashed_with_its_own_allow_list(git: Git, checkout: Path) -> None:
    """A later checkout that lets less into the image gives a tag the hash the tag's own does."""
    (checkout / ".dockerignore").write_text("*\n!app/\n!lib/\n")
    (checkout / "lib").mkdir()
    (checkout / "lib" / "util.py").write_text("HELPER = 1\n")
    commit_all(git, "lib")
    git("tag", "v2")
    at_the_tag = fingerprint(checkout).stdout
    (checkout / ".dockerignore").write_text("*\n!app/\n")
    commit_all(git, "no lib")
    assert fingerprint(checkout, "v2").stdout == at_the_tag
    assert fingerprint(checkout).stdout != at_the_tag


@pytest.mark.parametrize("argument", ["--output=x", "v1..HEAD", "no-such-commit"])
def test_only_one_commit_reaches_git(checkout: Path, argument: str) -> None:
    """An option, a range or an unknown name is refused; so are two commits."""
    refused = fingerprint(checkout, argument)
    assert refused.returncode == 1
    assert "is no commit" in refused.stderr
    assert fingerprint(checkout, "v1", "HEAD").returncode == 1
