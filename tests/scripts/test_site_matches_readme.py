"""The website says what the README on the repository's front page says (ADR 0113 point 7).

The README is the source for what OpenPlanner is and how to start it; the site's texts in
``site/content/{nl,en}.json`` repeat the quick start for visitors. A change to the README's
quick start fails here until both locales of the site carry it, so the two cannot drift
between releases.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text()


def _quick_start() -> str:
    """Return the README's quick start section."""
    match = re.search(r"^## Quick start.*?(?=^## )", README, re.S | re.M)
    assert match, "README.md has no Quick start section"
    return match.group(0)


def _first_shell_block() -> list[str]:
    block = re.search(r"```sh\n(.*?)```", _quick_start(), re.S)
    assert block, "the Quick start section has no sh block"
    return [line.strip() for line in block.group(1).splitlines() if line.strip()]


def _site(locale: str) -> dict[str, dict[str, str]]:
    data: dict[str, dict[str, str]] = json.loads(
        (ROOT / "site" / "content" / f"{locale}.json").read_text()
    )
    return data


@pytest.mark.parametrize("locale", ["nl", "en"])
def test_the_site_repeats_the_readmes_quick_start_commands(locale: str) -> None:
    command = _site(locale)["hero"]["command"]
    missing = [line for line in _first_shell_block() if line not in command]
    assert not missing, f"README quick start not on the site ({locale}): {missing}"


@pytest.mark.parametrize("locale", ["nl", "en"])
def test_the_site_names_the_demo_address_of_the_readme(locale: str) -> None:
    demo = re.search(r"http://127\.0\.0\.1:8000/g/\w+", _quick_start())
    assert demo, "the README's quick start names no demo booking page"
    assert demo.group(0) in _site(locale)["hero"]["command_label"]


@pytest.mark.parametrize("locale", ["nl", "en"])
def test_the_site_links_the_installation_guide_the_readme_names(locale: str) -> None:
    assert "docs/install.md" in _quick_start()
    assert "docs/install.md" in (ROOT / "site" / "content" / f"{locale}.json").read_text()
