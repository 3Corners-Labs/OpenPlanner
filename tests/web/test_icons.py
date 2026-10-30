"""The icon macro (``admin/_icons.html``): every name the portal uses has a branch (IC-1)."""

import re
from pathlib import Path

import pytest

from app.web.navigation import TOP_BAR, screens
from app.web.templates import SHARED_TEMPLATE_DIR, create_environment

ADMIN_TEMPLATES = Path("app/admin/templates")

ICON_CALL = re.compile(r'\bicon\("([a-z-]+)"\)')
"""A literal ``icon("name")`` call in a template."""

SHELL_ICONS = frozenset(
    {"tasks", "status", "search", "filter", "export", "import", "close", "menu", "more", "signout"}
)
"""The icons the refreshed shell adds (backlog item IC-1), named before a screen uses them."""


def _render(name: str) -> str:
    """Return the macro's output for ``name``, as the admin templates render it."""
    env = create_environment([ADMIN_TEMPLATES, SHARED_TEMPLATE_DIR])
    return str(env.get_template("admin/_icons.html").module.icon(name))  # type: ignore[attr-defined]


FALLBACK = _render("no-such-icon")
"""What an unknown name renders: the macro's last branch, never nothing."""


def _register_icons() -> set[str]:
    """Return every icon name the navigation register and the top bar use."""
    return {screen.icon for _, screen in screens()} | {link.icon for link in TOP_BAR}


def _template_icons() -> set[str]:
    """Return every icon name an admin template names literally."""
    return {
        name
        for path in sorted(ADMIN_TEMPLATES.rglob("*.html"))
        for name in ICON_CALL.findall(path.read_text())
    }


def _every_icon_name() -> list[str]:
    """Return every icon name the portal uses, so that the parametrised test lists them."""
    return sorted(_register_icons() | _template_icons() | SHELL_ICONS)


def test_an_unknown_name_is_told_apart_from_a_known_one() -> None:
    assert "<circle" in FALLBACK, "the fallback must render something, so a gap stays visible"
    assert _render("dashboard") != FALLBACK


def test_the_register_and_the_templates_name_icons() -> None:
    assert len(_register_icons()) > 10
    assert "plus" in _template_icons()


@pytest.mark.parametrize("name", _every_icon_name())
def test_every_icon_the_portal_uses_has_a_branch(name: str) -> None:
    rendered = _render(name)

    assert rendered != FALLBACK, f"add a branch for {name!r} to admin/_icons.html"
    assert 'aria-hidden="true"' in rendered
    assert 'viewBox="0 0 24 24"' in rendered
    assert 'stroke-width="2"' in rendered
    assert 'width="20" height="20"' in rendered
    assert "style=" not in rendered
    assert 'fill="none"' in rendered
