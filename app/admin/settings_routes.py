"""System status and Advanced settings (ADR 0042 §11, build steps 3 and 4).

One page for ``settings.platform.view`` (superuser and auditor):

- *System status*: every class A and B variable with whether it is set and where from, never
  its value; for a development or test installation, what production would refuse, in the
  words of the start-up check;
- every class C and D setting by topic, with its effective value, its source and a lock note
  naming the variable when the environment locks it. Secrets show only whether they are set.

With ``settings.platform.manage`` (superuser, recent login) the settings that build step 4
covers get a form: a new value within the portal bounds, or *Use the default* when the portal
holds one. ``LOG_LEVEL`` also takes the hours after which it falls back (at most 24). A
refusal is shown at the row, linked to its field; the submitted text is never echoed into
the audit log.

The page reloads the portal values first, so it always shows what the processes now apply.

    GET  /admin/settings              settings.platform.view
    POST /admin/settings/{key}        settings.platform.manage (recent login)
    POST /admin/settings/{key}/reset  settings.platform.manage (recent login)
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.adapters.live_settings import LiveSettings
from app.adapters.settings_report import SettingState
from app.admin.authz import AdminContext, RequirePermission, can_function
from app.admin.forms import CODE_MESSAGES, done_message, raise_refusal, read_form, redirect_done
from app.admin.pages import Locale, SameOrigin
from app.admin.pages import page as _page
from app.config import Settings
from app.domain.authorization import Permission
from app.domain.errors import DomainError
from app.domain.settings_registry import BY_KEY, LOG_LEVEL_MAX_EXPIRY_HOURS, SettingClass, Topic
from app.domain.settings_service import LOG_LEVEL, SettingsService, editable
from app.web.locale import RequestLocale
from app.web.settings_labels import (
    CLASS_LABELS,
    PROBLEM_LABELS,
    SETTING_LABELS,
    SOURCE_LABELS,
    TOPIC_LABELS,
)

router = APIRouter(prefix="/admin", include_in_schema=False)

URL = "/admin/settings"
View = Annotated[AdminContext, Depends(RequirePermission(Permission.SETTINGS_PLATFORM_VIEW))]
Change = Annotated[
    AdminContext, Depends(RequirePermission(Permission.SETTINGS_PLATFORM_MANAGE, fresh=True))
]

_ENVIRONMENT_ONLY = (SettingClass.BOOTSTRAP, SettingClass.DEPLOYMENT)


@dataclass(frozen=True)
class StatusRow:
    """One class A or B variable: whether it is set and where from, never its value."""

    variable: str
    class_label: str
    source: str
    is_set: bool


@dataclass(frozen=True)
class SettingRow:
    """One class C or D setting as the table shows it."""

    label: str
    variable: str
    value: str
    source: str
    locked: bool
    outside_bounds: bool
    portal_ignored: bool
    key: str = ""
    editable: bool = False
    input: str = ""
    choices: tuple[str, ...] = ()
    from_portal: bool = False
    expires: bool = False
    error: str | None = None


@dataclass(frozen=True)
class TopicSection:
    """The settings of one topic."""

    key: str
    heading: str
    rows: list[SettingRow]


@dataclass(frozen=True)
class Problem:
    """What production would refuse, translated."""

    variable: str
    text: str


@router.get("/settings", response_class=HTMLResponse)
async def advanced_settings(request: Request, locale: Locale, context: View) -> HTMLResponse:
    """Show System status and every portal setting with its value and source."""
    return await _overview(request, locale, context)


@router.post("/settings/{key}", response_class=HTMLResponse)
async def change_setting(
    request: Request, key: str, locale: Locale, _: SameOrigin, context: Change
) -> Response:
    """Change one setting within its portal bounds; the processes apply it within 10 s."""
    form = await read_form(request, locale)
    raw_hours = form.text("hours")
    hours = int(raw_hours) if raw_hours.isdigit() and len(raw_hours) <= 3 else None
    try:
        await _service(request).change(context.principal, key, form.text("value"), hours=hours)
    except DomainError as error:
        raise_refusal(error)
        return await _overview(request, locale, context, (key, error), 422)
    return redirect_done(URL, "saved", anchor=_anchor(key))


@router.post("/settings/{key}/reset", response_class=HTMLResponse)
async def reset_setting(
    request: Request, key: str, locale: Locale, _: SameOrigin, context: Change
) -> Response:
    """Remove the portal value, so the default (or the environment) applies again."""
    try:
        await _service(request).reset(context.principal, key)
    except DomainError as error:
        raise_refusal(error)
        return await _overview(request, locale, context, (key, error), 409)
    return redirect_done(URL, "saved", anchor=_anchor(key))


def _service(request: Request) -> SettingsService:
    """Return the application's settings service."""
    service: SettingsService = request.app.state.settings_service
    return service


def _anchor(key: str) -> str:
    """Return the row anchor of a registered key (anything else scrolls nowhere)."""
    return f"setting-{key}" if key in BY_KEY else ""


async def _overview(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    refusal: tuple[str, DomainError] | None = None,
    status: int = 200,
) -> HTMLResponse:
    """Render the page, with a refusal shown at its row."""
    _ = locale.gettext
    live: LiveSettings = request.app.state.live_settings
    await live.refresh()
    states = live.states()
    current: Settings = request.app.state.settings
    status_rows = [
        StatusRow(s.spec.env, _(CLASS_LABELS[s.spec.class_]), _(SOURCE_LABELS[s.source]), s.is_set)
        for s in states
        if s.spec.class_ in _ENVIRONMENT_ONLY
    ]
    problems = (
        []
        if current.environment == "prod"
        else [Problem(p.variable, _(PROBLEM_LABELS[p.code])) for p in current.production_problems()]
    )
    manage = can_function(context.principal)(Permission.SETTINGS_PLATFORM_MANAGE)
    errors = {}
    if refusal is not None:
        key, error = refusal
        errors[key] = _(CODE_MESSAGES.get(error.code, "error.invalid_request"))
    response = _page(
        request,
        locale,
        "admin/settings.html",
        context.principal,
        {
            "environment": current.environment,
            "refused": live.refused,
            "status": status_rows,
            "problems": problems,
            "sections": _sections(states, locale, manage=manage, errors=errors),
            "done": done_message(request, locale.gettext),
            "max_hours": LOG_LEVEL_MAX_EXPIRY_HOURS,
            "manage": manage,
            "unknown_error": next((m for k, m in errors.items() if k not in BY_KEY), None),
        },
    )
    response.status_code = status
    return response


def _sections(
    states: list[SettingState],
    locale: RequestLocale,
    *,
    manage: bool = False,
    errors: dict[str, str] | None = None,
) -> list[TopicSection]:
    """Group the class C and D settings by topic, in the registry's topic order."""
    _ = locale.gettext
    errors = errors or {}
    by_topic: dict[Topic, list[SettingRow]] = {}
    for state in states:
        if not state.spec.in_portal:
            continue
        by_topic.setdefault(state.spec.topic, []).append(
            SettingRow(
                _(SETTING_LABELS[state.spec.key]),
                state.spec.env,
                _value(state, locale),
                _(SOURCE_LABELS[state.source]),
                state.locked,
                state.outside_bounds,
                state.portal_ignored,
                key=state.spec.key,
                editable=manage and editable(state.spec) and not state.locked,
                input=_input(state),
                choices=state.spec.choices,
                from_portal=state.source.value == "portal",
                expires=state.spec.key == LOG_LEVEL,
                error=errors.get(state.spec.key),
            )
        )
    return [
        TopicSection(topic.value, _(TOPIC_LABELS[topic]), by_topic[topic])
        for topic in Topic
        if topic in by_topic
    ]


def _value(state: SettingState, locale: RequestLocale) -> str:
    """Write a value for the page: secrets as set or not set, booleans and empties in words."""
    _ = locale.gettext
    if state.spec.secret:
        return _("admin.settings.value.set") if state.is_set else _("admin.settings.value.not_set")
    if state.value is None:
        return _("admin.settings.value.empty")
    if isinstance(state.value, bool):
        return _("admin.settings.value.on") if state.value else _("admin.settings.value.off")
    return str(state.value)


def _input(state: SettingState) -> str:
    """Return the value as the form's input shows it (empty for a secret or no value)."""
    if state.spec.secret or state.value is None:
        return ""
    if isinstance(state.value, bool):
        return "true" if state.value else "false"
    return str(state.value)
