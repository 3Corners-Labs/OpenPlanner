"""What the screens of the capacity pools show of a pool (ADR 0121 points 6 and 11).

View models and form fields shared by the combined *Skills and capacity* page
(``app.admin.skills_capacity_page``) and the pages of one pool (``app.admin.capacity_routes``).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.admin.forms import Field, FormData
from app.admin.skill_inputs import SkillChoice, choices, skill_name, ticked_ids
from app.domain.capacity import PoolMode
from app.domain.capacity_service import FlowRef, PoolLine
from app.domain.skills import Skill
from app.web.locale import RequestLocale

CHOICE_FIELD = "guaranteed_skill_ids"
"""The id of the group of skill checkboxes, where an error about the choice links to."""


@dataclass(frozen=True)
class PoolRowView:
    """One pool as the list, the form and the confirmation page show it."""

    id: int
    name: str
    code: str
    legacy: bool
    tag: str
    skills: tuple[str, ...]
    flows: tuple[str, ...]
    hidden: str
    archived: bool


def flow_name(flow: FlowRef, locale: str, *, with_service: bool = False) -> str:
    """Return a flow as ``label`` or ``label (service)`` in the admin's language.

    The service is added only on request: a pool of one service names its flows alone, a counted
    pool several services use names whose flow each one is.
    """
    label = flow.labels.get(locale) or flow.labels.get("nl") or next(iter(flow.labels.values()))
    return f"{label} ({flow.service})" if with_service and flow.service else label


def _names_services(line: PoolLine) -> bool:
    """Whether the flows of ``line`` are named with their service: a counted pool of several.

    A legacy pool belongs to one service, so its flows never repeat it.
    """
    if line.pool.mode is PoolMode.LEGACY_GROUP:
        return False
    return line.hidden > 0 or len({f.service for f in line.flows}) > 1


def hidden_flows(count: int, locale: RequestLocale) -> str:
    """Return "and N flows of other services" for ``count`` flows the viewer may not see."""
    _ = locale.gettext
    if count == 0:
        return ""
    key = "admin.capacity.flows_hidden_one" if count == 1 else "admin.capacity.flows_hidden"
    return _(key, count=count)


def _tag(line: PoolLine, locale: RequestLocale) -> str:
    """Return the mode tag: existing (legacy), or own and shared for a counted pool."""
    _ = locale.gettext
    if line.pool.mode is PoolMode.LEGACY_GROUP:
        return _("admin.capacity.mode.legacy")
    return _("admin.capacity.mode.shared" if line.shared else "admin.capacity.mode.own")


def pool_row(line: PoolLine, locale: RequestLocale) -> PoolRowView:
    """Translate a pool with its skills and flows for the templates."""
    return PoolRowView(
        line.pool.id or 0,
        line.pool.name,
        line.pool.code or "",
        line.pool.mode is PoolMode.LEGACY_GROUP,
        _tag(line, locale),
        tuple(skill_name(s, locale.code) for s in line.skills),
        tuple(flow_name(f, locale.code, with_service=_names_services(line)) for f in line.flows),
        hidden_flows(line.hidden, locale),
        line.pool.archived_at is not None,
    )


def name_field(form: FormData, locale: RequestLocale, line: PoolLine | None) -> Field:
    """Return the name input, filled from the form or the stored pool."""
    _ = locale.gettext
    stored = line.pool.name if line else ""
    return Field(
        "pool_name",
        _("admin.capacity.name"),
        value=form.values.get("pool_name", stored),
        error=form.error_for("pool_name"),
        attributes={"maxlength": "80", "autocomplete": "off"},
    )


def pool_code_field(form: FormData, locale: RequestLocale, line: PoolLine | None) -> Field:
    """Return the code input: typed for a new pool, shown read-only for a stored one."""
    _ = locale.gettext
    if line is not None:
        coded = line.pool.code is not None
        return Field(
            "pool_code",
            _("admin.capacity.code"),
            value=line.pool.code or "",
            readonly=True,
            hint=_("admin.capacity.code_fixed" if coded else "admin.capacity.no_code"),
            attributes={} if coded else {"placeholder": "\u2013"},
        )
    return Field(
        "pool_code",
        _("admin.capacity.code"),
        value=form.text("pool_code"),
        hint=_("admin.capacity.code_hint"),
        error=form.error_for("pool_code"),
        attributes={"maxlength": "40", "spellcheck": "false", "autocapitalize": "none"},
    )


def pool_choices(
    skills: Sequence[Skill], stored: frozenset[int], form: FormData, locale: RequestLocale
) -> list[SkillChoice]:
    """Return the skills to choose from; the submitted ticks win over the ``stored`` ones."""
    return choices(skills, ticked_ids(form) if form.values else stored, locale.code)
