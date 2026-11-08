"""The groups list, *Manage → Groups* (ADR 0047 §4 and build step 5).

A cross-object list in the list pattern: a GET filter form (search on name or slug, and the
organisation), a count, a table with the shortfall chip, and a link to each group's page. It
never holds an edit form. The rows are the groups the matrix lets the admin view, read with
the database-side scope; the organisation filter offers only the organisations of those rows,
and a value outside them is ignored and never echoed (``docs/threat-model.md``).

    GET /admin/groups   group.view   the groups in the admin's scope
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse

from app.admin.authz import AdminContext, RequirePermission, admin_service, can_function
from app.admin.forms import done_message
from app.admin.list_search import ORGANISATION_PARAM, QUERY_PARAM, ListFilter, narrow
from app.admin.pages import Locale, page
from app.admin.shortfalls import ShortfallView, agenda_service, shortfall_view
from app.domain.agenda_service import ShortfallSummary
from app.domain.authorization import Permission
from app.domain.scheduling import GroupSummary
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)


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
class GroupFilterView(ListFilter):
    """The list's filter (``app.admin.list_search``) with the archived toggle of ADR 0084."""

    archived: bool = False

    @property
    def active(self) -> bool:
        """Whether anything narrows the list."""
        return super().active or self.archived


def narrow_groups(
    groups: list[GroupSummary], query: str, organisation: str, *, archived: bool = False
) -> tuple[list[GroupSummary], GroupFilterView]:
    """Return the groups matching ``query`` (name or slug) and ``organisation``, and the filter."""
    rows, found = narrow(
        groups,
        query,
        organisation,
        organisation_of=lambda g: (g.organisation_slug, g.organisation_name),
        fields=lambda g: (g.group.name, g.group.slug),
    )
    return rows, GroupFilterView(**vars(found), archived=archived)


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
    rows, filters = narrow_groups(
        found,
        request.query_params.get(QUERY_PARAM, ""),
        request.query_params.get(ORGANISATION_PARAM, ""),
        archived=archived,
    )
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
