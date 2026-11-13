"""Immediate work in the own queue of a service (ADR 0121 step 7): claim, start, complete.

Requests without an appointment are listed on the queue page under the appointments
(``queue_routes``); these routes act on them. Every route needs ``queue.work`` on the service;
``RequestQueue`` decides who may do what (the holder, or a supervisor for release and for
work in recovery) and judges the pool at the claim and at the start. A refusal is answered with
the queue and its reason, like the appointments' refusals.

    POST /admin/groups/{slug}/queue/requests/next             queue.work  (oldest one that fits)
    POST /admin/groups/{slug}/queue/requests/{id}/claim       queue.work  (free pick only)
    GET  /admin/groups/{slug}/queue/requests/{id}             queue.work  (holder only)
    POST /admin/groups/{slug}/queue/requests/{id}/start       queue.work  (holder)
    POST /admin/groups/{slug}/queue/requests/{id}/complete    queue.work  (holder; supervisor in
                                                                          recovery)
    POST /admin/groups/{slug}/queue/requests/{id}/release     queue.work  (holder, supervisor)

The pages show the first name and the answers an agent may see, never email or phone.
"""

from collections.abc import Awaitable
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import raise_refusal, redirect_done
from app.admin.group_routes import group_page
from app.admin.pages import Locale, SameOrigin
from app.admin.queue_routes import SHOWN_ON_THE_LIST, render_list
from app.admin.request_queue_views import claimed_view, queue
from app.domain.authorization import Permission
from app.domain.errors import DomainError
from app.domain.request_queue import ClaimedRequest
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

WorkQueue = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.QUEUE_WORK, group_param="slug")),
]


def _url(slug: str) -> str:
    """Return the queue's address."""
    return f"/admin/groups/{slug}/queue"


async def _done(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    attempt: Awaitable[object],
    done: str | None = None,
) -> Response:
    """Run ``attempt``; go to the claimed request (or the list with ``done``), or explain."""
    try:
        result = await attempt
    except DomainError as error:
        raise_refusal(error)
        if error.code not in SHOWN_ON_THE_LIST:
            raise
        return await render_list(request, locale, context, slug, error.code)
    if isinstance(result, ClaimedRequest):
        return RedirectResponse(f"{_url(slug)}/requests/{result.request.id}", status_code=303)
    return redirect_done(_url(slug), done or "saved")


@router.post("/groups/{slug}/queue/requests/next", response_class=HTMLResponse)
async def pick_up_next(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: WorkQueue
) -> Response:
    """Claim the oldest request without an appointment the agent may take."""
    attempt = queue(request).claim(context.principal, slug)
    return await _done(request, locale, context, slug, attempt)


@router.post("/groups/{slug}/queue/requests/{request_id}/claim", response_class=HTMLResponse)
async def claim(
    request: Request, locale: Locale, slug: str, request_id: int, _: SameOrigin, context: WorkQueue
) -> Response:
    """Claim one chosen request (only where the service allows free picking)."""
    attempt = queue(request).claim(context.principal, slug, request_id)
    return await _done(request, locale, context, slug, attempt)


@router.get("/groups/{slug}/queue/requests/{request_id}", response_class=HTMLResponse)
async def claimed(
    request: Request, locale: Locale, slug: str, request_id: int, context: WorkQueue
) -> Response:
    """Show a request the agent holds, with *Start* or *Complete*."""
    try:
        found = await queue(request).claimed(context.principal, slug, request_id, locale.code)
    except DomainError as error:
        raise_refusal(error)
        if error.code not in SHOWN_ON_THE_LIST:
            raise
        return await render_list(request, locale, context, slug, error.code)
    view = claimed_view(found, locale, context)
    return group_page(request, locale, context, "admin/queue_request.html", {"claimed": view})


@router.post("/groups/{slug}/queue/requests/{request_id}/start", response_class=HTMLResponse)
async def start(
    request: Request, locale: Locale, slug: str, request_id: int, _: SameOrigin, context: WorkQueue
) -> Response:
    """Start the treatment of a claimed request; its pool is judged again now."""
    attempt = queue(request).start(context.principal, slug, request_id)
    return await _done(request, locale, context, slug, attempt)


@router.post("/groups/{slug}/queue/requests/{request_id}/complete", response_class=HTMLResponse)
async def complete(
    request: Request, locale: Locale, slug: str, request_id: int, _: SameOrigin, context: WorkQueue
) -> Response:
    """Complete a request: its unit of the pool is released."""
    attempt = queue(request).complete(context.principal, slug, request_id)
    return await _done(request, locale, context, slug, attempt, "completed")


@router.post("/groups/{slug}/queue/requests/{request_id}/release", response_class=HTMLResponse)
async def release(
    request: Request, locale: Locale, slug: str, request_id: int, _: SameOrigin, context: WorkQueue
) -> Response:
    """Put a claimed request back in the line; its unit of the pool is released."""
    attempt = queue(request).release(context.principal, slug, request_id)
    return await _done(request, locale, context, slug, attempt, "released")
