"""The links at the end of the admin top bar: status, tasks, alerts and the manual (SH-2).

The links come from the navigation register (``app.web.navigation.TOP_BAR``) through the matrix:
one is shown only to an admin who may open what it leads to. A count is read once per request
(``load_top_counts``, a dependency of every admin page) and rendered on the server. A link
without a count source shows none: *Status* has no number, *Alerts* wait for the alert model of
ADR 0061 step 4. A count is a whole number and never a name or a booking.
"""

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from app.admin.authz import admin_service, session_principal
from app.i18n import BoundGettext
from app.web.help import HelpTopic
from app.web.navigation import TOP_BAR, Home, Screen

COUNTS_ATTRIBUTE = "top_counts"
"""Name on ``request.state`` under which the counts are kept, by the key of the link."""

HELP_KEY = "top.help"
"""The register key of the manual link, which opens the page's own topic when it has one."""

COUNT_LABELS = {"top.tasks": "admin.topbar.tasks.count"}
"""The words a screen reader gets for a link with a count; also the list of links that have one."""


@dataclass(frozen=True)
class TopLink:
    """A link of the top bar as the template draws it.

    Attributes:
        label: The visible text.
        href: Where it leads.
        current: Whether it is the page being shown.
        icon: The icon name from ``admin/_icons.html``.
        count: The number next to the label; 0 shows nothing.
        aria_label: The accessible name when there is a count (it starts with the label).
    """

    label: str
    href: str
    current: bool
    icon: str
    count: int = 0
    aria_label: str = ""


async def load_top_counts(request: Request) -> None:
    """Put the counts of the top bar on ``request.state`` for the logged-in admin.

    Only the number of running and failed background jobs has a source today, the same figure
    the *Running tasks* widget shows. Nothing is read for a visitor.
    """
    if session_principal(request) is None or not hasattr(request.app.state, "admin_service"):
        return
    active = await admin_service(request).active_jobs(1)
    setattr(request.state, COUNTS_ATTRIBUTE, {"top.tasks": active.total})


def _shown(link: Screen, can: Callable[..., bool], on_overview: set[str]) -> bool:
    """Whether the admin may open what ``link`` leads to (and sees the widget it points at)."""
    return can(link.permission) and (not link.widget or link.widget in on_overview)


def top_links(
    request: Request,
    translate: BoundGettext,
    can: Callable[..., bool],
    widgets: set[str],
    home: Home | None,
    help_topic: HelpTopic | None,
) -> list[TopLink]:
    """Return the links of the top bar for this admin, in register order.

    Args:
        request: The request, which carries the counts.
        translate: The page's gettext.
        can: The matrix as a function of the permission.
        widgets: The keys of the widgets on the admin's overview.
        home: Where the page lives in the register, to mark the current link.
        help_topic: The manual topic of the page; the manual link then opens it.
    """
    counts: dict[str, int] = getattr(request.state, COUNTS_ATTRIBUTE, {})
    links = []
    for link in TOP_BAR:
        if not _shown(link, can, widgets):
            continue
        current = home is not None and home.entry == link.key
        if link.key == HELP_KEY and help_topic is not None:
            href = f"/admin/help/{help_topic.slug}"
            links.append(TopLink(translate("help.page_link"), href, current, link.icon))
            continue
        label = translate(link.label_key)
        count = counts.get(link.key, 0)
        aria = translate(COUNT_LABELS[link.key], label=label, count=count) if count else ""
        links.append(TopLink(label, link.path, current, link.icon, count, aria))
    return links
