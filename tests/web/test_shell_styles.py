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


def test_the_side_navigation_stays_in_view_under_the_top_bar() -> None:
    """The menu is sticky with its own scroll (admin shell design, #209)."""
    nav = _rule(".admin-nav")

    assert "position: sticky" in nav
    assert "top: 3.5rem" in nav
    assert "height: calc(100vh - 3.5rem)" in nav
    assert "overflow-y: auto" in nav


# --- Page width framework (ADR 0080) --------------------------------------------------------

RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
"""An innermost CSS rule (also inside an at-rule): selector and declarations."""

FIXED_CAPS = frozenset(
    {
        ".admin-scope summary",
        ".admin-scope__panel",
        ".admin-profile__panel",
        ".card--message",
        ".timeline__hint",
        ".admin-public__header",
        ".admin-public__card",
        ".brand-preview__logo",
        ".brand-preview__header--logo .brand-preview__logo",
        ".more",
        # A settings row's field keeps a readable width inside its column (one form per topic).
        ".admin .setting__value input",
        ".admin .setting__value select",
    }
)
"""Components that cap their own width in rem or ch. A page container never does (ADR 0080)."""

NOT_A_CAP = {"none", "100%"}


def _rules() -> list[tuple[str, str]]:
    """Return every (selector, declarations) pair of ``admin.css``, comments removed."""
    sheet = CSS_COMMENT.sub("", (STATIC / "admin.css").read_text())
    return [(" ".join(sel.split()), body) for sel, body in RULE.findall(sheet)]


def _token(name: str) -> str:
    """Return the value of the custom property ``name`` in the first ``:root``-like block."""
    found = re.search(rf"{re.escape(name)}:\s*([^;]+);", (STATIC / "admin.css").read_text())
    assert found, f"no token {name}"
    return found.group(1).strip()


def test_the_two_page_widths_are_tokens_and_wide_is_wider() -> None:
    standard = float(_token("--adm-width-standard").removesuffix("rem"))
    wide = float(_token("--adm-width-wide").removesuffix("rem"))

    assert standard < wide


def test_the_page_container_takes_its_width_from_the_tokens() -> None:
    assert "max-width: var(--adm-width-standard)" in _rule(".admin-main")
    assert "max-width: var(--adm-width-wide)" in _rule('.admin-main[data-width="wide"]')
    assert "--adm-content-width" not in (STATIC / "admin.css").read_text()


def test_a_form_in_a_wide_page_keeps_the_reading_width() -> None:
    capped = _rule('.admin-main[data-width="wide"] > form.card')

    assert "max-width: var(--adm-width-standard)" in capped


def test_only_known_components_cap_their_own_width() -> None:
    caps = {
        sel
        for sel, body in _rules()
        for value in re.findall(r"(?<![-\w])max-width:\s*([^;]+);", body)
        if value.strip() not in NOT_A_CAP and "var(--adm-width" not in value
        for sel in sel.split(", ")
        if not sel.startswith(".admin-bottombar")
    }

    assert caps - FIXED_CAPS == set(), (
        "a new width cap: a page container takes --adm-width-standard or --adm-width-wide "
        "(declared in app/web/navigation.py, ADR 0080); a component may be added to FIXED_CAPS"
    )


# --- The weekly schedule is seven columns or one (ADR 0080) -----------------------------------


def test_the_week_never_wraps_into_six_and_one() -> None:
    week = _rule(".week")

    assert "auto-fit" not in week
    assert "auto-fill" not in week
    assert "container-type: inline-size" in _rule(".week-frame")
    assert "repeat(7, minmax(0, 1fr))" in CSS_COMMENT.sub("", (STATIC / "admin.css").read_text())


def test_the_week_markup_sits_in_its_frame() -> None:
    template = (ADMIN_TEMPLATES / "_week.html").read_text()

    assert '<div class="week-frame">' in template
    assert template.index("week-frame") < template.index('class="week"')


def test_the_icon_rail_is_written_once_and_switches_on_the_menu_width() -> None:
    """The rail serves the tablet's default and the admin's choice from the menu button (#208).

    A container query on the menu's own width carries the rail rules, so neither the tablet
    media query nor the collapsed state repeats them; those two only set the width.
    """
    sheet = CSS_COMMENT.sub("", (STATIC / "admin.css").read_text())

    assert ".admin-nav {\n  container-type: inline-size;" in sheet
    assert sheet.count("@container (max-width: 6rem)") == 1
    assert sheet.count(".admin-nav a:is(:hover, :focus-visible) > span") == 1
    assert sheet.count("--adm-nav-width: var(--adm-rail-width)") == 2
    for state in ("collapse", "expand"):
        assert f".admin-nav-toggle__button--{state}" in sheet


def test_the_menu_button_and_the_mark_form_one_cluster_on_the_left() -> None:
    """The product mark sits next to the menu button, not in the middle of the bar (ADR 0047).

    The bar no longer spreads its children; the end block pushes itself right, and the button's
    form is as wide as the rail and starts at the window's edge, so it stands above the icons.
    """
    bar = _rule(".admin-topbar")
    toggle = _rule(".admin-nav-toggle")

    assert "justify-content" not in bar
    assert "margin-left: auto" in _rule(".admin-topbar__end")
    assert "margin-right: auto" not in _rule(".admin-scope")
    assert "width: var(--adm-rail-width)" in toggle
    assert "margin: 0 0 0 calc(-1 * var(--adm-topbar-pad))" in toggle
    assert "justify-content: center" in toggle
    assert _token("--adm-rail-width") == "3.75rem"
