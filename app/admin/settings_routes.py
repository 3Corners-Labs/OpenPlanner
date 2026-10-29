"""System status and Advanced settings, read-only (ADR 0042 §11, build step 3).

One page for ``settings.platform.view`` (superuser and auditor):

- *System status*: every class A and B variable with whether it is set and where from, never
  its value; for a development or test installation, what production would refuse, in the
  words of the start-up check;
- every class C and D setting by topic, with its effective value, its source and a lock note
  naming the variable when the environment locks it. Secrets show only whether they are set.

The page reloads the portal values first, so it always shows what the processes now apply.

    GET /admin/settings     settings.platform.view
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse

from app.adapters.live_settings import LiveSettings
from app.adapters.settings_report import SettingState
from app.admin.authz import AdminContext, RequirePermission
from app.admin.pages import Locale
from app.admin.pages import page as _page
from app.config import Settings
from app.domain.authorization import Permission
from app.domain.settings_registry import SettingClass, Topic
from app.web.locale import RequestLocale
from app.web.settings_labels import (
    CLASS_LABELS,
    PROBLEM_LABELS,
    SETTING_LABELS,
    SOURCE_LABELS,
    TOPIC_LABELS,
)

router = APIRouter(prefix="/admin", include_in_schema=False)

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
async def advanced_settings(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.SETTINGS_PLATFORM_VIEW))],
) -> HTMLResponse:
    """Show System status and every portal setting with its value and source."""
    _ = locale.gettext
    live: LiveSettings = request.app.state.live_settings
    await live.refresh()
    states = live.states()
    current: Settings = request.app.state.settings
    status = [
        StatusRow(s.spec.env, _(CLASS_LABELS[s.spec.class_]), _(SOURCE_LABELS[s.source]), s.is_set)
        for s in states
        if s.spec.class_ in _ENVIRONMENT_ONLY
    ]
    problems = (
        []
        if current.environment == "prod"
        else [Problem(p.variable, _(PROBLEM_LABELS[p.code])) for p in current.production_problems()]
    )
    return _page(
        request,
        locale,
        "admin/settings.html",
        context.principal,
        {
            "environment": current.environment,
            "status": status,
            "problems": problems,
            "sections": _sections(states, locale),
        },
    )


def _sections(states: list[SettingState], locale: RequestLocale) -> list[TopicSection]:
    """Group the class C and D settings by topic, in the registry's topic order."""
    _ = locale.gettext
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
