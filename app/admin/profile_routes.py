"""The admin's own profile: the screen name a participant sees (ADR 0123 step 5d).

    GET  /admin/rights/profile              rights.view    My profile
    POST /admin/rights/profile/screen-name  rights.view    set or clear the own screen name

*My profile* is a sub-tab of *My rights*, next to *My sessions*: personal, so for every admin and
without a permission of its own (ADR 0098 point 9a). It shows the name the identity provider gave
(read-only; it is not what participants see) and the screen name with its form, the same field
and refusal texts as the manager's form (``app.admin.screen_name_form``).

The write is for the logged-in person only. The staff record is found from the session's own
subject; no number is in the path or the form, so there is nothing to point at another person.
The rule, the erasure check and the audit event are those of ``StaffScreenNames`` (step 5c); the
event's actor and subject are the same person. A text that is not a screen name answers the
page again (422) with the typed text and the reason; neither goes into a log or an event.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission, screen_names
from app.admin.forms import FormData, done_message, raise_refusal, read_form, redirect_done
from app.admin.pages import Locale, SameOrigin
from app.admin.pages import page as _page
from app.admin.screen_name_form import DONE, FIELD, done_key, refusal_error, screen_name_field
from app.domain.authorization import Permission
from app.domain.errors import DomainError
from app.domain.screen_name import ScreenNameError
from app.domain.staff import StaffStatus
from app.domain.staff_screen_names import ScreenNameResult
from app.web.locale import RequestLocale
from app.web.protection import RejectedRequest

router = APIRouter(prefix="/admin", include_in_schema=False)

PROFILE_PATH = "/admin/rights/profile"
"""The *My profile* page, a sub-tab of *My rights*."""

Viewer = Annotated[AdminContext, Depends(RequirePermission(Permission.RIGHTS_VIEW))]


async def profile_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    *,
    form: FormData | None = None,
    status: int = 200,
) -> HTMLResponse:
    """Render *My profile*; ``form`` re-shows a refused text with its error (``status`` 422)."""
    member = await screen_names(request).own(context.principal.subject)
    editable = member is not None and member.status is not StaffStatus.ERASED
    field = (
        screen_name_field(request, locale, member.screen_name if member else None, form)
        if editable
        else None
    )
    response = _page(
        request,
        locale,
        "admin/my_profile.html",
        context.principal,
        {
            "account_name": context.principal.display_name,
            "screen_name_field": field,
            "no_record": member is None,
            "done": done_message(request, locale.gettext, only=DONE),
        },
    )
    response.status_code = status
    return response


@router.get("/rights/profile", response_class=HTMLResponse)
async def my_profile(request: Request, locale: Locale, context: Viewer) -> HTMLResponse:
    """Show the admin's own account name and the form for the screen name."""
    return await profile_page(request, locale, context)


@router.post("/rights/profile/screen-name", response_model=None)
async def save_own_screen_name(
    request: Request, locale: Locale, _: SameOrigin, context: Viewer
) -> Response:
    """Set the logged-in person's own screen name, or clear it with an empty text."""
    form = await read_form(request, locale)
    try:
        result = await screen_names(request).set_own_screen_name(
            context.principal, form.text(FIELD)
        )
    except ScreenNameError as error:
        form.errors.append(refusal_error(locale, error))
        return await profile_page(request, locale, context, form=form, status=422)
    except DomainError as error:
        raise_refusal(error)
        raise
    if result in (ScreenNameResult.ERASED, ScreenNameResult.NO_RECORD):
        raise RejectedRequest(409)
    return redirect_done(PROFILE_PATH, done_key(result), anchor="screen-name")
