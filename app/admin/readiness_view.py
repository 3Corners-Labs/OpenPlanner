"""The readiness checklist as the overview tabs show it (ADR 0047 §5, build step 5).

Turns a ``Checklist`` from ``app.domain.readiness`` into lines with a label, a status in words
(never colour alone) and a link to the tab that fixes the item. A line links only when the
matrix lets the reader open that tab (ADR 0014); otherwise it says who can. The links are
built from the register's paths and the authorised object's slug, never from the request.
"""

from collections.abc import Callable
from dataclasses import dataclass

from starlette.requests import Request

from app.domain.authorization import LEVELS, Level, Permission, Principal, Target, decide
from app.domain.readiness import Checklist, Item, ReadinessService, State
from app.web.locale import RequestLocale

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
    State.NOT_NEEDED: "admin.readiness.state.not_needed",
}
"""The status of a line, in words."""

TEXTS_OWN = "admin.readiness.texts_own"
TEXTS_DEFAULT = "admin.readiness.texts_default"

GROUP_LINKS: dict[Item, tuple[str, Permission]] = {
    Item.STAFFING: ("/admin/groups/{slug}/staffing", Permission.SCHEDULE_MANAGE),
    Item.TEXTS: ("/admin/groups/{slug}/messages", Permission.TEXTS_MANAGE),
    Item.CHANNELS: ("/admin/organisations/{org}/channels", Permission.INTEGRATIONS_MANAGE),
    Item.CHECK: ("/admin/groups/{slug}/settings#check", Permission.GROUP_EDIT),
}
"""Where a group's item is fixed, and the permission that page needs."""

ORGANISATION_LINKS: dict[Item, tuple[str, Permission]] = {
    Item.CHANNELS: ("/admin/organisations/{org}/channels", Permission.INTEGRATIONS_MANAGE),
    Item.BRANDING: ("/admin/organisations/{org}/branding", Permission.BRANDING_MANAGE),
    Item.CONTACT: ("/admin/organisations/{org}/contacts", Permission.ORGANISATION_CLOSURE),
}
"""Where an organisation's item is fixed, and the permission that page needs."""


@dataclass(frozen=True)
class LineView:
    """One line of the checklist as the template shows it.

    Attributes:
        label: What the item is about.
        status: Where it stands, in words.
        detail: An extra word (own or standard texts), or empty.
        done: Whether nothing is left to do (done or not needed).
        href: The tab that fixes it, when the reader may open it; else empty.
    """

    label: str
    status: str
    detail: str
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
    links: dict[Item, tuple[str, Permission]],
    allowed: Callable[[Permission], bool],
    slugs: dict[str, str],
) -> ChecklistView:
    """Return ``checklist`` with labels, statuses and the links ``allowed`` grants.

    Args:
        checklist: The lines from the domain.
        locale: For the texts.
        links: Path template and permission per item.
        allowed: Whether the reader may open a page that needs the permission, on this object.
        slugs: The placeholders of the paths (``slug``, ``org``) with the authorised values.
    """
    _ = locale.gettext
    lines = []
    for line in checklist.lines:
        path, permission = links[line.item]
        detail = ""
        if line.item is Item.TEXTS:
            detail = _(TEXTS_OWN if line.own else TEXTS_DEFAULT)
        lines.append(
            LineView(
                _(LABELS[line.item]),
                _(STATES[line.state]),
                detail,
                line.state is not State.MISSING,
                path.format(**slugs) if allowed(permission) else "",
            )
        )
    return ChecklistView(lines, checklist.missing)


def readiness_service(request: Request) -> ReadinessService:
    """Return the application's readiness service."""
    service: ReadinessService = request.app.state.readiness_service
    return service


def allowed_on(principal: Principal, organisation: str, group: str | None) -> Callable[..., bool]:
    """Return whether ``principal`` may use a permission on this group or organisation.

    The target is the object the checklist belongs to, at the permission's level, so a group
    admin is not offered the organisation's channels unless the matrix grants them there.
    """

    def allowed(permission: Permission) -> bool:
        level = LEVELS[permission]
        if level is Level.GLOBAL:
            target = Target.everything()
        elif level is Level.ORGANISATION:
            target = Target.for_organisation(organisation)
        elif group is None:
            return False  # a group's page is never fixed from an organisation's checklist
        else:
            target = Target.for_group(group, organisation)
        return decide(principal, permission, target).allowed

    return allowed
