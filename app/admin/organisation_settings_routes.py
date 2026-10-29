"""Organisation settings: the class D values of one organisation (ADR 0042 step 5).

An organisation admin (``settings.organisation.manage``, own organisations; the superuser for
every one) sets the time format, the admin portal's time zone, and the staff, shortfall and
rebook periods of their organisation. Without a value the platform value applies, which the
superuser sets on *Advanced settings*. The environment lock binds only the platform value.

    GET  /admin/organisation-settings                       organisations in scope
    GET  /admin/organisations/{org}/settings                the six settings with source
    POST /admin/organisations/{org}/settings/{key}          set (recent login)
    POST /admin/organisations/{org}/settings/{key}/reset    back to the platform value
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.adapters.live_settings import LiveSettings
from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import CODE_MESSAGES, done_message, raise_refusal, read_form, redirect_done
from app.admin.pages import Locale, SameOrigin
from app.admin.pages import page as _page
from app.admin.settings_routes import SettingRow, _input, _value
from app.domain.authorization import Permission
from app.domain.errors import DomainError
from app.domain.settings_registry import BY_KEY
from app.domain.settings_service import SettingsService
from app.web.locale import RequestLocale
from app.web.settings_labels import SETTING_LABELS, SOURCE_LABELS

router = APIRouter(prefix="/admin", include_in_schema=False)

List = Annotated[AdminContext, Depends(RequirePermission(Permission.SETTINGS_ORGANISATION_MANAGE))]
View = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.SETTINGS_ORGANISATION_MANAGE, organisation_param="org")),
]
Change = Annotated[
    AdminContext,
    Depends(
        RequirePermission(
            Permission.SETTINGS_ORGANISATION_MANAGE, organisation_param="org", fresh=True
        )
    ),
]


def _service(request: Request) -> SettingsService:
    """Return the application's settings service."""
    service: SettingsService = request.app.state.settings_service
    return service


def _url(org: str) -> str:
    """Return the settings page of organisation ``org``."""
    return f"/admin/organisations/{org}/settings"


@router.get("/organisation-settings", response_class=HTMLResponse)
async def organisations(request: Request, locale: Locale, context: List) -> HTMLResponse:
    """List the organisations whose settings the admin may change."""
    found = await _service(request).organisations(context.principal)
    return _page(
        request,
        locale,
        "admin/organisation_settings_list.html",
        context.principal,
        {"organisations": found},
    )


@router.get("/organisations/{org}/settings", response_class=HTMLResponse)
async def organisation_settings(
    request: Request, locale: Locale, org: str, context: View
) -> HTMLResponse:
    """Show one organisation's settings, each with its value and where it comes from."""
    return await _overview(request, locale, context, org)


@router.post("/organisations/{org}/settings/{key}", response_class=HTMLResponse)
async def change_organisation_setting(
    request: Request, locale: Locale, org: str, key: str, _: SameOrigin, context: Change
) -> Response:
    """Set one value for the organisation."""
    form = await read_form(request, locale)
    try:
        await _service(request).change_organisation(context.principal, org, key, form.text("value"))
    except DomainError as error:
        raise_refusal(error)
        return await _overview(request, locale, context, org, (key, error), 422)
    return redirect_done(_url(org), "saved", anchor=_anchor(key))


@router.post("/organisations/{org}/settings/{key}/reset", response_class=HTMLResponse)
async def reset_organisation_setting(
    request: Request, locale: Locale, org: str, key: str, _: SameOrigin, context: Change
) -> Response:
    """Remove the organisation's value, so the platform value applies again."""
    try:
        await _service(request).reset_organisation(context.principal, org, key)
    except DomainError as error:
        raise_refusal(error)
        return await _overview(request, locale, context, org, (key, error), 409)
    return redirect_done(_url(org), "saved", anchor=_anchor(key))


def _anchor(key: str) -> str:
    """Return the row anchor of a registered key (anything else scrolls nowhere)."""
    return f"setting-{key}" if key in BY_KEY else ""


async def _overview(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    refusal: tuple[str, DomainError] | None = None,
    status: int = 200,
) -> HTMLResponse:
    """Render the organisation's settings, with a refusal shown at its row."""
    _ = locale.gettext
    organisation = await _service(request).organisation(context.principal, org)
    live: LiveSettings = request.app.state.live_settings
    await live.refresh()
    errors: dict[str, str] = {}
    if refusal is not None:
        errors[refusal[0]] = _(CODE_MESSAGES.get(refusal[1].code, "error.invalid_request"))
    rows = [
        SettingRow(
            _(SETTING_LABELS[state.spec.key]),
            state.spec.env,
            _value(state, locale),
            _(SOURCE_LABELS[state.source]),
            False,
            False,
            state.portal_ignored,
            key=state.spec.key,
            editable=True,
            input=_input(state),
            choices=state.spec.choices,
            from_portal=state.source.value == "organisation",
            error=errors.get(state.spec.key),
        )
        for state in live.organisation_states(organisation.id)
    ]
    response = _page(
        request,
        locale,
        "admin/organisation_settings.html",
        context.principal,
        {
            "organisation": organisation,
            "rows": rows,
            "done": done_message(request, locale.gettext),
            "unknown_error": next((m for k, m in errors.items() if k not in BY_KEY), None),
        },
    )
    response.status_code = status
    return response
