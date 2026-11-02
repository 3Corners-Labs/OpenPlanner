"""Viewing and ending admin sessions (ADR 0098 point 9, step 3).

A person sees their own sessions and may end the others; an administrator ends all sessions
of a person from that person's page in the staff overview. The session itself is the record
of ``app.domain.admin_session_service``; these routes only show it and ask the service.

    GET  /admin/rights/sessions                 rights.view          My sessions
    POST /admin/rights/sessions/end-others      rights.view          end the other sessions
    POST /admin/staff/{member_id}/sessions/end  staff.sessions.end   end all of a person's

*My sessions* needs no permission of its own: it only concerns the own sessions and every admin
holds ``rights.view``. The list shows the login time and the last activity, and marks the
current session; it never shows the session reference, an address or device data. The
administrator's action is an organisation-level permission applied to the staff overview's own
scope: a person outside it is refused (403) and the refusal is audited. A change needs a
recent login, like every change in the portal.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.admin.authz import (
    AccessDenied,
    AdminContext,
    RequirePermission,
    session_policy,
    session_service,
)
from app.admin.forms import done_message, redirect_done
from app.admin.pages import Locale, SameOrigin, admin_moment
from app.admin.pages import page as _page
from app.admin.session import current_session, end_session
from app.domain.authorization import Permission, scope_for
from app.web.protection import RejectedRequest

router = APIRouter(prefix="/admin", include_in_schema=False)

SESSIONS_PATH = "/admin/rights/sessions"
"""The *My sessions* page, a sub-tab of *My rights*."""

Viewer = Annotated[AdminContext, Depends(RequirePermission(Permission.RIGHTS_VIEW))]
EndPerson = Annotated[
    AdminContext, Depends(RequirePermission(Permission.STAFF_SESSIONS_END, fresh=True))
]


@dataclass(frozen=True)
class SessionView:
    """One live session as the page shows it (no reference, address or device).

    Attributes:
        login: When the person logged in.
        last_seen: The last recorded activity.
        current: Whether this is the session of the request.
    """

    login: str
    last_seen: str
    current: bool


@router.get("/rights/sessions", response_class=HTMLResponse)
async def my_sessions(request: Request, locale: Locale, context: Viewer) -> HTMLResponse:
    """List the admin's own live sessions, the current one marked."""
    current = current_session(request)
    live = await session_service(request).own(context.principal.subject, session_policy(request))
    views = [
        SessionView(
            admin_moment(request, locale, s.login_at),
            admin_moment(request, locale, s.last_seen_at),
            current is not None and s.id_hash == current.id_hash,
        )
        for s in live
    ]
    return _page(
        request,
        locale,
        "admin/my_sessions.html",
        context.principal,
        {
            "sessions": views,
            "others": sum(1 for v in views if not v.current),
            "done": done_message(request, locale.gettext),
        },
    )


@router.post("/rights/sessions/end-others", response_model=None)
async def end_other_sessions(request: Request, _: SameOrigin, __: Viewer) -> Response:
    """End every session of the admin except the current one."""
    current = current_session(request)
    if current is None:
        return RedirectResponse(SESSIONS_PATH, status_code=303)
    count = await session_service(request).end_others(current)
    return redirect_done(SESSIONS_PATH, "sessions_ended_others", count)


@router.post("/staff/{member_id}/sessions/end", response_model=None)
async def end_sessions_of_person(
    request: Request, member_id: int, _: SameOrigin, context: EndPerson
) -> Response:
    """End all sessions of a person in the administrator's scope (ADR 0098 point 9b).

    Outside the scope the answer is 403 (and an audit event) for an organisation admin; for
    the superuser, who sees everyone, an unknown number is a 404. Ending one's own sessions
    ends the current one too and leaves the portal.
    """
    service = session_service(request)
    ended = await service.end_of_person(context.principal, member_id)
    if ended is None:
        if scope_for(context.principal, Permission.STAFF_SESSIONS_END).everything:
            raise RejectedRequest(404)
        raise AccessDenied
    if ended.subject == context.principal.subject:
        await end_session(request, service)
        return RedirectResponse("/admin/logged-out", status_code=303)
    return redirect_done(f"/admin/staff/{member_id}", "sessions_ended_person", ended.count)
