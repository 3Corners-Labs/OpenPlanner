"""The admin shell is styled: every class its templates use has a rule (backlog SH-2, step 2b).

An element whose class has no rule renders as plain HTML (a bulleted list where buttons were
meant). The test reads the shell templates and the two stylesheets; it needs no browser.
"""

import re
from pathlib import Path

import pytest

ADMIN_TEMPLATES = Path("app/admin/templates/admin")
SHARED_TEMPLATES = Path("app/web/templates")
STATIC = Path("app/web/static")

SHELL_TEMPLATES = [
    ADMIN_TEMPLATES / "layout.html",
    ADMIN_TEMPLATES / "_shell.html",
    ADMIN_TEMPLATES / "_environment.html",
    ADMIN_TEMPLATES / "_maintenance.html",
    ADMIN_TEMPLATES / "_update_banner.html",
    ADMIN_TEMPLATES / "_scope_filter.html",
    SHARED_TEMPLATES / "_language_switcher.html",
]
"""Everything the logged-in frame includes, whatever the page."""

CLASS_ATTRIBUTE = re.compile(r'\bclass="([^"]*)"')
"""A literal ``class="..."`` attribute; Jinja expressions inside it are cut out first."""

CSS_CLASS = re.compile(r"\.(-?[_a-zA-Z][\w-]*)")
CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
JINJA = re.compile(r"\{[{%].*?[}%]\}", re.DOTALL)


def _used(path: Path) -> set[str]:
    """Return the class names ``path`` names literally."""
    text = JINJA.sub(" ", path.read_text())
    names = {name for value in CLASS_ATTRIBUTE.findall(text) for name in value.split()}
    # ``update-banner--{{ kind }}`` leaves the prefix of a class built at render time.
    return {name for name in names if not name.endswith("-")}


def _styled() -> set[str]:
    """Return every class name a selector of the shared or admin stylesheet mentions."""
    sheets = (STATIC / "site.css").read_text() + (STATIC / "admin.css").read_text()
    return set(CSS_CLASS.findall(CSS_COMMENT.sub("", sheets)))


def _rule(selector: str) -> str:
    """Return the declarations of the first rule of ``admin.css`` that is exactly ``selector``."""
    sheet = CSS_COMMENT.sub("", (STATIC / "admin.css").read_text())
    found = re.search(rf"(?:^|\n){re.escape(selector)}\s*\{{([^}}]*)\}}", sheet)
    assert found, f"no rule for {selector}"
    return found.group(1)


@pytest.mark.parametrize("path", SHELL_TEMPLATES, ids=str)
def test_every_class_of_the_shell_has_a_rule(path: Path) -> None:
    assert _used(path) - _styled() == set()


def test_the_check_finds_a_class_without_a_rule(tmp_path: Path) -> None:
    template = tmp_path / "t.html"
    template.write_text('<ul class="admin-brand {{ x }}"><li class="nowhere-at-all kind--{{ y }}">')

    assert _used(template) - _styled() == {"nowhere-at-all"}


def test_the_top_bar_is_a_fixed_height_sticky_row() -> None:
    bar = _rule(".admin-topbar")

    assert "position: sticky" in bar
    assert "top: 0" in bar
    assert "height: 3.5rem" in bar
    assert "flex-wrap: nowrap" in bar


def test_the_profile_summary_has_no_marker_or_chevron() -> None:
    sheet = (STATIC / "admin.css").read_text()

    assert ".admin-profile summary::after" not in sheet
    assert "list-style: none" in _rule(".admin-profile summary")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_a_chosen_theme_sets_the_colour_scheme(theme: str) -> None:
    assert f"color-scheme: {theme}" in _rule(f'body.admin[data-theme="{theme}"]')


def test_the_icon_rail_is_written_once_and_switches_on_the_menu_width() -> None:
    """The rail serves the tablet's default and the admin's choice from the menu button (#208).

    A container query on the menu's own width carries the rail rules, so neither the tablet
    media query nor the collapsed state repeats them; those two only set the width.
    """
    sheet = CSS_COMMENT.sub("", (STATIC / "admin.css").read_text())

    assert ".admin-nav {\n  container-type: inline-size;" in sheet
    assert sheet.count("@container (max-width: 6rem)") == 1
    assert sheet.count(".admin-nav a:is(:hover, :focus-visible) > span") == 1
    assert sheet.count("--adm-nav-width: 3.75rem") == 2
    for state in ("collapse", "expand"):
        assert f".admin-nav-toggle__button--{state}" in sheet
