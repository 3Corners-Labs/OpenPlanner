"""Setting a staff member's screen name on their page (ADR 0123 step 5c).

    POST /admin/staff/{member_id}/screen-name    staff.manage

The screen name is the name the participant of a call in our own page sees for the staff member,
beside the organisation's word for staff; without one the participant sees the word alone, never
the account's name (``embedded_call_staff_name``). It is set by someone with ``staff.manage`` for
a person in the staff overview's scope, in the section *Screen name* of the person's page (the
page itself, its list mark and its manual topic are in ``app.admin.staff_routes``). An empty text
clears the name.

The route needs a same-origin POST and ``staff.manage``; the service asks the matrix again and
that the person is one the administrator sees. A person outside it answers like the other staff
actions (403 and a denied audit event; 404 for the superuser, who sees everyone, and an unknown
number). A text that is not a screen name answers the page again with the reason (422); nothing is
echoed into a log or an event. The audit event ``staff.screen_name_changed`` says that the name
was set or cleared and never what it is.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.admin.authz import AccessDenied, AdminContext, RequirePermission
from app.admin.forms import raise_refusal, read_form, redirect_done
from app.admin.pages import Locale, SameOrigin
from app.admin.screen_name_form import refusal_error
from app.admin.staff_routes import member_page
from app.domain.authorization import Permission, scope_for
from app.domain.errors import DomainError
from app.domain.screen_name import ScreenNameError
from app.domain.staff_screen_names import ScreenNameResult, StaffScreenNames
from app.web.protection import RejectedRequest

router = APIRouter(prefix="/admin", include_in_schema=False)

Manage = Annotated[AdminContext, Depends(RequirePermission(Permission.STAFF_MANAGE))]


def screen_names(request: Request) -> StaffScreenNames:
    """Return the application's screen-name service."""
    service: StaffScreenNames = request.app.state.staff_names
    return service


@router.post("/staff/{member_id}/screen-name", response_model=None)
async def save_screen_name(
    request: Request, locale: Locale, member_id: int, _: SameOrigin, context: Manage
) -> Response:
    """Set the person's screen name, or clear it with an empty text."""
    form = await read_form(request, locale)
    try:
        result = await screen_names(request).set_screen_name(
            context.principal, member_id, form.text("screen_name")
        )
    except ScreenNameError as error:
        form.errors.append(refusal_error(locale, error))
        return await member_page(request, locale, context, member_id, form=form, status=422)
    except DomainError as error:
        raise_refusal(error)
        raise
    if result is ScreenNameResult.OUT_OF_SCOPE:
        if scope_for(context.principal, Permission.STAFF_MANAGE).everything:
            raise RejectedRequest(404)
        raise AccessDenied
    if result is ScreenNameResult.ERASED:
        raise RejectedRequest(409)
    done = (
        "staff_screen_name_saved"
        if result is ScreenNameResult.DONE
        else "staff_screen_name_unchanged"
    )
    return redirect_done(f"/admin/staff/{member_id}", done, anchor="screen-name")
