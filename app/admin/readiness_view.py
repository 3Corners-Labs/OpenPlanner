"""The readiness checklist as the overview tabs show it (ADR 0047 §5, build step 5).

Turns a ``Checklist`` from ``app.domain.readiness`` into lines with a label, a status in words
(never colour alone) and a link to the screen that fixes the item. Each item names a screen of
the navigation register, so the link follows the register's path, and it shows only when the
same predicate that draws the object's tabs lets the reader open that screen (ADR 0014). Paths
are filled from the authorised object's slugs, never from the request.
"""

from collections.abc import Callable
from dataclasses import dataclass

from starlette.requests import Request

from app.domain.readiness import Checklist, Item, ReadinessService, State
from app.web.locale import RequestLocale
from app.web.navigation import Screen, screen

LABELS: dict[Item, str] = {
    Item.STAFFING: "admin.readiness.staffing",
    Item.TEXTS: "admin.readiness.texts",
    Item.CHANNELS: "admin.readiness.channels",
    Item.CHECK: "admin.readiness.check",
    Item.BRANDING: "admin.readiness.branding",
    Item.CONTACT: "admin.readiness.contact",
}
"""The label of each item (literal keys, so the catalogue test sees them)."""

STATES: dict[State, str] = {
    State.DONE: "admin.readiness.state.done",
    State.MISSING: "admin.readiness.state.missing",
    State.DEFAULTS: "admin.readiness.state.defaults",
    State.NOT_NEEDED: "admin.readiness.state.not_needed",
}
"""The status of a line, in words."""

FIXED_ON: dict[Item, tuple[str, str]] = {
    Item.STAFFING: ("group.planning", ""),
    Item.TEXTS: ("group.texts", ""),
    Item.CHANNELS: ("organisation.channels", ""),
    Item.CHECK: ("group.settings", "#check"),
    Item.BRANDING: ("organisation.participants", ""),
    Item.CONTACT: ("organisation.contact", ""),
}
"""The register screen (and section anchor) where each item is fixed."""


@dataclass(frozen=True)
class LineView:
    """One line of the checklist as the template shows it.

    Attributes:
        label: What the item is about.
        status: Where it stands, in words.
        done: Whether nothing is left to do (anything but missing).
        href: The screen that fixes it, when the reader may open it; else empty.
    """

    label: str
    status: str
    done: bool
    href: str


@dataclass(frozen=True)
class ChecklistView:
    """A checklist for the template: its lines and how many are missing."""

    lines: list[LineView]
    missing: int


def checklist_view(
    checklist: Checklist,
    locale: RequestLocale,
    shows: Callable[[Screen], bool],
    slugs: dict[str, str],
) -> ChecklistView:
    """Return ``checklist`` with labels, statuses and the links ``shows`` grants.

    Args:
        checklist: The lines from the domain.
        locale: For the texts.
        shows: The predicate the object's tabs are drawn with (``object_tabs``).
        slugs: The path placeholders (``slug``, ``org``) with the authorised object's values.
    """
    _ = locale.gettext
    lines = []
    for line in checklist.lines:
        key, anchor = FIXED_ON[line.item]
        target = screen(key)
        href = target.path.format(**slugs) + anchor if shows(target) else ""
        lines.append(
            LineView(
                _(LABELS[line.item]),
                _(STATES[line.state]),
                line.state is not State.MISSING,
                href,
            )
        )
    return ChecklistView(lines, checklist.missing)


def readiness_service(request: Request) -> ReadinessService:
    """Return the application's readiness service."""
    service: ReadinessService = request.app.state.readiness_service
    return service
