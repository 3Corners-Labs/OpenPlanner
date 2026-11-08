"""*Add widget* and *Edit overview*: the admin arranges their own overview (ADR 0096 step 3).

Two sub-pages of the overview, server-rendered forms without script (ADR 0016, 0118), every
control a button that works with the keyboard alone (no drag, WCAG 2.5.7). They offer only the
widgets the matrix grants (``allowed_keys``) and change the signed-in admin's own layout one
step at a time through ``OverviewLayoutService``; nobody else's layout is ever read or changed.
The layout is a personal preference, like the theme: changes are not audited (ADR 0096 §1).

    GET  /admin/overview/add      dashboard.view   the widgets that can be added
    POST /admin/overview/add      dashboard.view   add one at the end
    GET  /admin/overview/edit     dashboard.view   the layout with up, down and remove
    POST /admin/overview/edit     dashboard.view   move or remove one widget
    POST /admin/overview/reset    dashboard.view   back to the role's default layout
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission, can_function, overview_layout_service
from app.admin.forms import done_message, read_form, redirect_done
from app.admin.pages import Locale, SameOrigin, page
from app.domain.authorization import Permission
from app.domain.errors import DomainError
from app.domain.overview_layout import addable, effective
from app.web.locale import RequestLocale
from app.web.widgets import OVERVIEW_PATH, allowed_keys, default_keys, widget

router = APIRouter(prefix="/admin", include_in_schema=False)

ADD_PATH = "/admin/overview/add"
EDIT_PATH = "/admin/overview/edit"

View = Annotated[AdminContext, Depends(RequirePermission(Permission.DASHBOARD_VIEW))]
"""Opening the overview is all a person needs to arrange their own."""

STEPS = ("up", "down", "remove")
"""The changes *Edit overview* offers per widget."""

REFUSALS: dict[str, str] = {
    "widget": "admin.layout.error.widget",
    "widget_count": "admin.layout.error.full",
    "offset": "admin.layout.error.widget",
    "staff": "admin.layout.error.staff",
}
"""The message of each refused change, by the field the domain names."""


@dataclass(frozen=True)
class WidgetRow:
    """One widget on a layout page.

    Attributes:
        key: The widget's key, sent back by its buttons.
        title: Its title in the page's language.
        first: Whether it is first (no *Up*).
        last: Whether it is last (no *Down*).
    """

    key: str
    title: str
    first: bool = False
    last: bool = False


def _title(locale: RequestLocale, key: str) -> str:
    """Return the widget's title; the key is always one of the register's."""
    item = widget(key)
    return locale.gettext(item.title_key) if item else key


def _keys(context: AdminContext) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the admin's default layout and the widgets the matrix grants them."""
    principal = context.principal
    return default_keys(principal), allowed_keys(can_function(principal))


async def _page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    template: str,
    error: str | None = None,
) -> HTMLResponse:
    """Render one of the two pages, with a refused change's message when there is one."""
    default, allowed = _keys(context)
    stored = await overview_layout_service(request).stored(context.principal.subject)
    shown = effective(stored, default, allowed)
    rows = [
        WidgetRow(key, _title(locale, key), i == 0, i == len(shown) - 1)
        for i, key in enumerate(shown)
    ]
    offer = [WidgetRow(key, _title(locale, key)) for key in addable(shown, allowed)]
    maximum = overview_layout_service(request).maximum
    response = page(
        request,
        locale,
        template,
        context.principal,
        {
            "rows": rows,
            "offer": offer,
            "own": stored is not None,
            "full": len(shown) >= maximum,
            "maximum": maximum,
            "error": locale.gettext(error) if error else None,
            "done": done_message(request, locale.gettext),
        },
    )
    if error:
        response.status_code = 422
    return response


def _refusal(error: DomainError) -> str:
    """Return the message key of a refused change."""
    return REFUSALS.get(error.field or "", "admin.layout.error.widget")


@router.get("/overview/add", response_class=HTMLResponse)
async def add_form(request: Request, locale: Locale, context: View) -> HTMLResponse:
    """List the widgets the admin may add and does not show yet."""
    return await _page(request, locale, context, "admin/overview_add.html")


@router.post("/overview/add", response_class=HTMLResponse)
async def add_widget(request: Request, locale: Locale, _: SameOrigin, context: View) -> Response:
    """Add one widget at the end of the admin's own layout, then show the overview."""
    form = await read_form(request, locale)
    default, allowed = _keys(context)
    try:
        await overview_layout_service(request).add(
            context.principal.subject, form.text("widget"), default, allowed
        )
    except DomainError as error:
        return await _page(request, locale, context, "admin/overview_add.html", _refusal(error))
    # The key passed the register check above, so the anchor names a real widget only.
    return redirect_done(OVERVIEW_PATH, "widget_added", anchor=f"widget-{form.text('widget')}")


@router.get("/overview/edit", response_class=HTMLResponse)
async def edit_form(request: Request, locale: Locale, context: View) -> HTMLResponse:
    """Show the admin's overview as a list with *Up*, *Down* and *Remove* per widget."""
    return await _page(request, locale, context, "admin/overview_edit.html")


@router.post("/overview/edit", response_class=HTMLResponse)
async def edit_layout(request: Request, locale: Locale, _: SameOrigin, context: View) -> Response:
    """Move one widget a place up or down, or remove it."""
    form = await read_form(request, locale)
    step, key = form.text("step"), form.text("widget")
    subject = context.principal.subject
    service = overview_layout_service(request)
    default, allowed = _keys(context)
    try:
        if step == "remove":
            await service.remove(subject, key, default, allowed)
        elif step in STEPS:
            offset = -1 if step == "up" else 1
            await service.move(subject, key, offset, default, allowed)
        else:
            return await _page(
                request, locale, context, "admin/overview_edit.html", "admin.layout.error.widget"
            )
    except DomainError as error:
        return await _page(request, locale, context, "admin/overview_edit.html", _refusal(error))
    if step == "remove":
        return redirect_done(EDIT_PATH, "widget_removed", anchor="layout-heading")
    # The key passed the layout check above; focus stays on the moved widget (WCAG 2.4.3).
    return redirect_done(EDIT_PATH, "widget_moved", anchor=f"layout-{key}")


@router.post("/overview/reset", response_class=HTMLResponse)
async def reset_layout(request: Request, _: SameOrigin, context: View) -> Response:
    """Delete the admin's own layout, so the default of their role applies again (§4)."""
    await overview_layout_service(request).reset(context.principal.subject)
    return redirect_done(OVERVIEW_PATH, "layout_reset")
