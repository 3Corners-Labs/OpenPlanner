"""Suspending, reinstating and erasing a person in the staff overview (ADR 0046 step 2).

    POST /admin/staff/{member_id}/suspend    staff.manage, fresh login
    POST /admin/staff/{member_id}/reinstate  staff.manage, fresh login
    POST /admin/staff/{member_id}/erase      staff.erase (superuser, locked), fresh login

Suspending blocks the portal for that person whatever their claims say and ends their
sessions through the register of ADR 0098; reinstating lifts it. Erasing removes the name,
roles and memberships of a suspended person; the subject stays for the audit log. The rules
(nobody suspends themselves, only a superuser touches a superuser) and the audit events are
``app.domain.staff_lifecycle``. A person outside the administrator's scope answers 403 and a
denied audit event, like every other staff action; for the superuser, who sees everyone, an
unknown number is a 404. Suspending or erasing one's own record is refused.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.admin.authz import AccessDenied, AdminContext, RequirePermission
from app.admin.forms import redirect_done
from app.admin.pages import SameOrigin
from app.domain.authorization import Permission, scope_for
from app.domain.staff_lifecycle import LifecycleResult, Result, StaffLifecycleService
from app.web.protection import RejectedRequest

router = APIRouter(prefix="/admin", include_in_schema=False)

Manage = Annotated[AdminContext, Depends(RequirePermission(Permission.STAFF_MANAGE, fresh=True))]
Erase = Annotated[AdminContext, Depends(RequirePermission(Permission.STAFF_ERASE, fresh=True))]


def lifecycle_service(request: Request) -> StaffLifecycleService:
    """Return the application's staff lifecycle service."""
    service: StaffLifecycleService = request.app.state.staff_lifecycle_service
    return service


def _answer(result: LifecycleResult, member_id: int, done: str, everything: bool) -> Response:
    """Turn a result into the redirect, or the refusal for a request the form never sends."""
    if result.outcome is Result.OUT_OF_SCOPE:
        if everything:
            raise RejectedRequest(404)
        raise AccessDenied
    if result.outcome in (Result.SELF, Result.SUPERUSER):
        raise AccessDenied
    if not result.ok:
        raise RejectedRequest(409)
    return redirect_done(f"/admin/staff/{member_id}", done)


@router.post("/staff/{member_id}/suspend", response_model=None)
async def suspend(request: Request, member_id: int, _: SameOrigin, context: Manage) -> Response:
    """Suspend a person in the administrator's scope and end their sessions."""
    result = await lifecycle_service(request).suspend(context.principal, member_id)
    everything = scope_for(context.principal, Permission.STAFF_MANAGE).everything
    return _answer(result, member_id, "staff_suspended", everything)


@router.post("/staff/{member_id}/reinstate", response_model=None)
async def reinstate(request: Request, member_id: int, _: SameOrigin, context: Manage) -> Response:
    """Lift a suspension."""
    result = await lifecycle_service(request).reinstate(context.principal, member_id)
    everything = scope_for(context.principal, Permission.STAFF_MANAGE).everything
    return _answer(result, member_id, "staff_reinstated", everything)


@router.post("/staff/{member_id}/erase", response_model=None)
async def erase(request: Request, member_id: int, _: SameOrigin, context: Erase) -> Response:
    """Erase a suspended person's record (the superuser only)."""
    result = await lifecycle_service(request).erase(context.principal, member_id)
    return _answer(result, member_id, "staff_erased", True)
