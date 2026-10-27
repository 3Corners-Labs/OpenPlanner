"""The manual in the portal (ADR 0033): the list of topics and one topic per screen.

The text is the Markdown in ``docs/manual`` (``app.web.help``), the same files GitHub shows.
Every admin may read every topic, because the manual is public; the list only offers the
screens the admin can open, from the matrix.

    GET /admin/help             dashboard.view   topics of the screens in the admin's reach
    GET /admin/help/{topic}     dashboard.view   one topic in the admin's language
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse

from app.admin.authz import AdminContext, RequirePermission, can_function
from app.admin.pages import Locale, message, page
from app.domain.authorization import Permission
from app.web.help import HELP_PATH, TOPICS, shipped_manual
from app.web.help import topic as find_topic

router = APIRouter(prefix="/admin", include_in_schema=False)

Reader = Annotated[AdminContext, Depends(RequirePermission(Permission.DASHBOARD_VIEW))]


@dataclass(frozen=True)
class TopicLink:
    """One entry of the topic list."""

    title: str
    href: str


@router.get("/help", response_class=HTMLResponse)
async def topics(request: Request, locale: Locale, context: Reader) -> HTMLResponse:
    """List the manual topics of the screens the admin can open."""
    can = can_function(context.principal)
    manual = shipped_manual()
    links = [
        TopicLink(found.title, f"{HELP_PATH}/{t.slug}")
        for t in TOPICS
        if can(t.permission) and (found := manual.page(t.slug, locale.code)) is not None
    ]
    return page(request, locale, "admin/help_list.html", context.principal, {"topics": links})


@router.get("/help/{topic}", response_class=HTMLResponse)
async def topic_page(request: Request, locale: Locale, topic: str, context: Reader) -> HTMLResponse:
    """Show one topic; a topic that does not exist is not found."""
    found = shipped_manual().page(topic, locale.code) if find_topic(topic) else None
    if found is None:
        return message(request, locale, 404, "error.not_found.title", "error.not_found.text")
    return page(request, locale, "admin/help_page.html", context.principal, {"manual": found})
