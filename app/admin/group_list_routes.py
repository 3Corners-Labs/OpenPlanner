"""The groups list, *Manage → Groups* (ADR 0047 §4 and build step 5).

A cross-object list in the list pattern: a GET filter form (search on name or slug, and the
organisation), a count, a table with the shortfall chip, and a link to each group's page. It
never holds an edit form. The rows are the groups the matrix lets the admin view, read with
the database-side scope; the organisation filter offers only the organisations of those rows,
and a value outside them is ignored and never echoed (``docs/threat-model.md``).

    GET /admin/groups   group.view   the groups in the admin's scope
"""

from dataclasses import dataclass, replace
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse

from app.admin.authz import AdminContext, RequirePermission, admin_service, can_function
from app.admin.forms import Option, done_message
from app.admin.pages import Locale, page
from app.admin.shortfalls import ShortfallView, agenda_service, shortfall_view
from app.domain.agenda_service import ShortfallSummary
from app.domain.authorization import Permission
from app.domain.scheduling import GroupSummary
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

MAX_QUERY_LENGTH = 100
"""Longest search text the list reads; a group name is shorter, the rest is cut off."""

QUERY_PARAM = "q"
ORGANISATION_PARAM = "org"


@dataclass(frozen=True)
class GroupRowView:
    """One row of the list."""

    name: str
    href: str
    organisation_name: str
    organisation_href: str | None
    minutes: int
    shortfall: ShortfallView | None
    public_href: str


@dataclass(frozen=True)
class GroupFilterView:
    """The filter form: what was typed or chosen, and the organisations to choose from.

    The search text only narrows the rows. The page never shows it again, so a personal-data
    value typed into the address bar is not reflected (ADR 0063 §3).
    """

    query: str
    organisation: str
    organisations: list[Option]
    archived: bool = False
    """ADR 0084: the archived groups instead of the ones in use."""

    @property
    def active(self) -> bool:
        """Whether anything narrows the list."""
        return bool(self.query or self.organisation or self.archived)


def narrow(
    groups: list[GroupSummary], query: str, organisation: str
) -> tuple[list[GroupSummary], GroupFilterView]:
    """Return the groups that match ``query`` and ``organisation``, and the checked filter.

    ``organisation`` counts only when it is the organisation of one of ``groups``; any other
    value is dropped. ``query`` is cut to ``MAX_QUERY_LENGTH`` and matched case-insensitively
    against the name and the slug.
    """
    organisations = {g.organisation_slug: g.organisation_name for g in groups}
    chosen = organisation if organisation in organisations else ""
    text = query[:MAX_QUERY_LENGTH].strip()
    needle = text.casefold()
    rows = [
        g
        for g in groups
        if (not chosen or g.organisation_slug == chosen)
        and (not needle or needle in g.group.name.casefold() or needle in g.group.slug)
    ]
    options = [Option(slug, name) for slug, name in organisations.items()]
    return rows, GroupFilterView(text, chosen, options)


@router.get("/groups", response_class=HTMLResponse)
async def groups(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.GROUP_VIEW))],
) -> HTMLResponse:
    """List the groups in the admin's scope, narrowed by the filter form."""
    principal = context.principal
    archived = request.query_params.get(ARCHIVED_PARAM) == "1"
    found = await admin_service(request).groups(principal, archived=archived)
    rows, filters = narrow(
        found,
        request.query_params.get(QUERY_PARAM, ""),
        request.query_params.get(ORGANISATION_PARAM, ""),
    )
    filters = replace(filters, archived=archived)
    summaries = await agenda_service(request).summaries(principal)
    opens_organisations = can_function(principal)(Permission.ORGANISATION_VIEW)
    return page(
        request,
        locale,
        "admin/groups.html",
        principal,
        {
            "rows": [
                _row(g, summaries.get(g.group.slug), opens_organisations, locale) for g in rows
            ],
            "filters": filters,
            "total": len(found),
            "done": done_message(request, locale.gettext),
        },
    )


def _row(
    summary: GroupSummary,
    shortfalls: ShortfallSummary | None,
    opens_organisations: bool,
    locale: RequestLocale,
) -> GroupRowView:
    """Return the row of ``summary``; the shortfall chip only when there is one."""
    group = summary.group
    chip = shortfall_view(shortfalls, group, locale) if shortfalls and shortfalls.count else None
    return GroupRowView(
        group.name,
        f"/admin/groups/{group.slug}",
        summary.organisation_name,
        f"/admin/organisations/{summary.organisation_slug}" if opens_organisations else None,
        group.slot_minutes,
        chip,
        f"/g/{group.slug}",
    )


ARCHIVED_PARAM = "archived"
"""``?archived=1`` lists the archived groups (ADR 0084), read-only."""
