"""The preferences of the profile menu and the top bar: theme, menu width and alert choices.

Kept in the session (step 2b). One route takes the choice and sends the admin back to the page
it was made on (ADR 0061 §7 for the alert preferences; the menu width comes from the admin
shell design: the top bar's menu button collapses the side navigation to the icon rail or
expands it). A GET, not a POST: the choice changes nothing but the
own session, so maintenance (ADR 0038) must not refuse it and the page may offer it as a form
of submit buttons without JavaScript.

    GET /admin/preferences    dashboard.view   store theme, menu width, alerts; 303 to ``next``

A value is stored only when it is one of the fixed choices; anything else is ignored and never
echoed. ``next`` goes through ``safe_next``, so the redirect stays inside the portal.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import RedirectResponse

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import safe_next
from app.admin.session import choose_alert_prefs, choose_nav, choose_theme
from app.domain.authorization import Permission

router = APIRouter(prefix="/admin", include_in_schema=False)

PREFERENCES_PATH = "/admin/preferences"
"""The route itself; a ``next`` that points here goes to the overview instead (no loop)."""

ALERT_FORM_PARAM = "alert_prefs"
"""Marker field of the alert preferences form (its box may arrive unticked)."""

Member = Annotated[AdminContext, Depends(RequirePermission(Permission.DASHBOARD_VIEW))]


@router.get("/preferences", response_model=None)
async def choose(request: Request, _: Member) -> RedirectResponse:
    """Store the submitted theme, menu width or alert choices in the session; return to ``next``."""
    query = request.query_params
    choose_theme(request, query.get("theme"))
    choose_nav(request, query.get("nav"))
    choose_alert_prefs(
        request,
        sent=query.get(ALERT_FORM_PARAM) == "1",
        banner=query.get("alert_banner") == "1",
        popups=query.get("alert_popups"),
    )
    target = safe_next(query.get("next"))
    if target.split("?", 1)[0] == PREFERENCES_PATH:
        target = "/admin/"
    return RedirectResponse(target, status_code=303)
