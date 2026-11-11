"""What the screens of the flows show of a flow (ADR 0121 step 2d).

View models, form fields and the texts of the closed lists (intake modes, contact channels,
handling actions, dispatch, completion, the six readiness checks and their fourteen reasons).
Every text is a literal catalogue key in one of the tables below, so the catalogue tests see it
and a test fails for a member of an enum without text. Nothing here decides anything: the
readiness checklist and the table of combinations are the domain's, the views only say them.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.admin.flow_inputs import extra_name, offer_name
from app.admin.forms import Field, FormData
from app.admin.skill_inputs import SkillChoice, choices, skill_label
from app.domain import service_flows as flows
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.flow_service import FlowChoices, FlowDraftInput, FlowLine
from app.domain.scheduling import MAX_SLOT_MINUTES, MIN_SLOT_MINUTES, SLOT_STEP_MINUTES
from app.domain.service_flows import (
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    FlowState,
    HandlingAction,
    IntakeMode,
    InteractionKind,
    ReadinessCheck,
    ReadinessItem,
    ReadinessPolicy,
    ReadinessReason,
)
from app.domain.skills import Skill
from app.web.locale import RequestLocale

INTAKE_LABELS: Mapping[IntakeMode, str] = {
    IntakeMode.APPOINTMENT: "admin.flows.intake.appointment",
    IntakeMode.ON_DEMAND: "admin.flows.intake.on_demand",
}
"""The words of the entry paths."""

KIND_LABELS: Mapping[InteractionKind, str] = {
    InteractionKind.VIDEO: "admin.flows.kind.video",
    InteractionKind.TELEPHONE: "admin.flows.kind.telephone",
    InteractionKind.IN_PERSON: "admin.flows.kind.in_person",
    InteractionKind.NONE: "admin.flows.kind.none",
}
"""The words of the contact channels."""

KIND_HINTS: Mapping[InteractionKind, str] = {
    InteractionKind.VIDEO: "admin.flows.kind.video_hint",
    InteractionKind.TELEPHONE: "admin.flows.kind.telephone_hint",
    InteractionKind.IN_PERSON: "admin.flows.kind.in_person_hint",
    InteractionKind.NONE: "admin.flows.kind.none_hint",
}
"""One line per contact channel on what it means."""

READINESS_TEXTS: Mapping[ReadinessPolicy, str] = {
    ReadinessPolicy.EXPLICIT_CHECK_IN: "admin.flows.ready.explicit_check_in",
    ReadinessPolicy.MEDIA_PRESENCE: "admin.flows.ready.media_presence",
    ReadinessPolicy.AUTOMATIC: "admin.flows.ready.automatic",
}
"""When a request is ready for handling, per readiness policy."""

ACTION_LABELS: Mapping[HandlingAction, str] = {
    HandlingAction.OPEN_VIDEO: "admin.flows.action.open_video",
    HandlingAction.VIEW_REQUEST: "admin.flows.action.view_request",
    HandlingAction.CALL: "admin.flows.action.call",
    HandlingAction.ASSIGN_WORKSTATION: "admin.flows.action.assign_workstation",
}
"""The first action of the operator, per handling action."""

DISPATCH_LABELS: Mapping[DispatchKind, str] = {
    DispatchKind.OWN_QUEUE: "admin.flows.dispatch.own_queue",
    DispatchKind.CONTACT_CENTER: "admin.flows.dispatch.contact_center",
}
"""Where requests are dispatched to staff."""

COMPLETION_LABELS: Mapping[CompletionPolicy, str] = {
    CompletionPolicy.OPERATOR: "admin.flows.completion.operator",
    CompletionPolicy.PROVIDER_EVENT: "admin.flows.completion.provider_event",
    CompletionPolicy.LEGACY_VIDEO_OBSERVATION: "admin.flows.completion.legacy_video_observation",
}
"""What marks a request as done (the legacy observation is shown, never offered)."""

STATE_LABELS: Mapping[FlowState, str] = {
    FlowState.DRAFT: "admin.flows.state.draft",
    FlowState.ACTIVE: "admin.flows.state.active",
    FlowState.ARCHIVED: "admin.flows.state.archived",
}
"""The state of a flow as a word, so colour is never the only signal."""

CHECK_TITLES: Mapping[ReadinessCheck, str] = {
    ReadinessCheck.DATA: "admin.flows.check.data",
    ReadinessCheck.CAPACITY: "admin.flows.check.capacity",
    ReadinessCheck.SKILLS: "admin.flows.check.skills",
    ReadinessCheck.DISPATCH: "admin.flows.check.dispatch",
    ReadinessCheck.COMPLETION: "admin.flows.check.completion",
    ReadinessCheck.RETENTION: "admin.flows.check.retention",
}
"""The title of each readiness check."""

REASON_TEXTS: Mapping[ReadinessReason, str] = {
    ReadinessReason.LABELS_INVALID: "admin.flows.reason.labels_invalid",
    ReadinessReason.INTAKE_MISSING: "admin.flows.reason.intake_missing",
    ReadinessReason.POOL_MISSING: "admin.flows.reason.pool_missing",
    ReadinessReason.POOL_ARCHIVED: "admin.flows.reason.pool_archived",
    ReadinessReason.DURATION: "admin.flows.reason.duration",
    ReadinessReason.POOL_DOES_NOT_GUARANTEE: "admin.flows.reason.pool_does_not_guarantee",
    ReadinessReason.SUBJECT_UNKNOWN: "admin.flows.reason.subject_unknown",
    ReadinessReason.KIND_MISSING: "admin.flows.reason.kind_missing",
    ReadinessReason.DISPATCH_MISSING: "admin.flows.reason.dispatch_missing",
    ReadinessReason.ACTION_NOT_ALLOWED: "admin.flows.reason.action_not_allowed",
    ReadinessReason.LOCATION_MISSING: "admin.flows.reason.location_missing",
    ReadinessReason.NOT_BUILT_YET: "admin.flows.reason.not_built_yet",
    ReadinessReason.COMPLETION_MISSING: "admin.flows.reason.completion_missing",
    ReadinessReason.COMPLETION_LEGACY_ONLY: "admin.flows.reason.completion_legacy_only",
}
"""One sentence per reason a check fails."""


@dataclass(frozen=True)
class FlowRowView:
    """One flow of the list.

    Attributes:
        code: The flow's code, its address.
        name: The name in the admin's language.
        other_name: The name in the other language.
        intake: The entry paths as words.
        channel: The contact channel as a word, empty when not chosen yet.
        pool: The capacity pool's name, empty when not chosen yet.
        skills: The names of the skills the flow requires.
        state: The state as a word.
        in_progress: Whether an active or published flow has a draft next to it.
        legacy: Whether it is a service's existing way of working: shown, not editable.
        archived: Whether the flow is archived.
    """

    code: str
    name: str
    other_name: str
    intake: str
    channel: str
    pool: str
    skills: tuple[str, ...]
    state: str
    in_progress: bool
    legacy: bool
    archived: bool


@dataclass(frozen=True)
class CheckView:
    """A checkbox of the editor."""

    name: str
    label: str
    checked: bool
    disabled: bool = False


@dataclass(frozen=True)
class OptionView:
    """An option of a select; one that is not built yet is listed but cannot be chosen."""

    value: str
    label: str
    selected: bool
    disabled: bool = False


@dataclass(frozen=True)
class SelectView:
    """A select of the editor with its visible label, hint and error."""

    name: str
    label: str
    options: tuple[OptionView, ...]
    hint: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class KindView:
    """One contact channel of the radio group."""

    value: str
    label: str
    hint: str
    checked: bool


@dataclass(frozen=True)
class ExtraView:
    """One extra skill of a subject as a checkbox."""

    id: int
    name: str
    label: str
    checked: bool
    archived: bool


@dataclass(frozen=True)
class SubjectView:
    """One subject of the service in the table of the editor."""

    code: str
    label: str
    offer_name: str
    offered: bool
    skills: tuple[ExtraView, ...]


@dataclass(frozen=True)
class ReadoutView:
    """What the saved contact channel means: when a request is ready, and the first action."""

    ready: str
    action: str


@dataclass(frozen=True)
class ReadinessRow:
    """One line of the readiness checklist."""

    title: str
    word: str
    missing: bool
    sentence: str


@dataclass(frozen=True)
class ReadinessView:
    """The readiness checklist as the aside shows it."""

    heading: str
    summary: str
    ready: bool
    rows: tuple[ReadinessRow, ...]


def _label(labels: Mapping[str, str], locale: str) -> str:
    """Return a flow's name in ``locale``; Dutch, else the code's stand-in, when it has none."""
    return labels.get(locale) or labels.get("nl") or ""


def pool_name(pool: CapacityPool | None, locale: RequestLocale) -> str:
    """Return a pool's name; the pool of an existing service is named by words, not its name."""
    _ = locale.gettext
    if pool is None:
        return ""
    if pool.mode is PoolMode.LEGACY_GROUP:
        return _("admin.flows.pool.legacy")
    return pool.name


def flow_row(line: FlowLine, locale: RequestLocale) -> FlowRowView:
    """Translate a flow of the list for the template."""
    _ = locale.gettext
    flow, revision = line.flow, line.revision
    other = "en" if locale.code == "nl" else "nl"
    kind = revision.interaction_kind
    modes = sorted(revision.intake_modes, key=lambda m: m.value)
    return FlowRowView(
        flow.code,
        _label(revision.labels, locale.code) or flow.code,
        _label(revision.labels, other)
        if _label(revision.labels, other) != _label(revision.labels, locale.code)
        else "",
        ", ".join(_(INTAKE_LABELS[m]) for m in modes),
        _(KIND_LABELS[kind]) if kind is not None else "",
        pool_name(line.pool, locale),
        tuple(skill_label(s, locale.code) for s in line.skills),
        _(STATE_LABELS[flow.state]),
        flow.draft is not None and flow.current is not None,
        line.legacy,
        flow.state is FlowState.ARCHIVED,
    )


def readiness_view(items: Sequence[ReadinessItem], locale: RequestLocale) -> ReadinessView:
    """Translate the readiness checklist: a title, a state as a word and a sentence per item."""
    _ = locale.gettext
    rows = []
    for item in items:
        reason = item.reason
        if item.ok or reason is None:
            word, sentence, missing = _("admin.flows.ready_word.ok"), "", False
        else:
            follows = reason is ReadinessReason.NOT_BUILT_YET
            word = _(
                "admin.flows.ready_word.follows" if follows else "admin.flows.ready_word.missing"
            )
            sentence, missing = _(REASON_TEXTS[reason]), True
        rows.append(ReadinessRow(_(CHECK_TITLES[item.code]), word, missing, sentence))
    done = sum(1 for item in items if item.ok)
    ready = flows.can_activate(items)
    return ReadinessView(
        _("admin.flows.ready_heading.ready" if ready else "admin.flows.ready_heading.not_ready"),
        _("admin.flows.ready_count", done=done, total=len(items)),
        ready,
        tuple(rows),
    )


def readout(kind: InteractionKind | None, locale: RequestLocale) -> ReadoutView | None:
    """Return what the contact channel ``kind`` means (from the closed table), none for no kind."""
    _ = locale.gettext
    if kind is None:
        return None
    combination = flows.COMBINATIONS[kind]
    first = " / ".join(_(ACTION_LABELS[a]) for a in HandlingAction if a in combination.actions)
    return ReadoutView(
        _("admin.flows.readout.ready", policy=_(READINESS_TEXTS[combination.readiness])),
        _("admin.flows.readout.action", action=first),
    )


def _text_field(
    form: FormData, name: str, label: str, value: str, hint: str | None = None
) -> Field:
    """Return a text input of the editor, filled from the form when it was posted."""
    return Field(
        name,
        label,
        value=form.values.get(name, value) if form.values else value,
        hint=hint,
        error=form.error_for(name),
        attributes={"maxlength": "80", "autocomplete": "off"},
    )


def name_fields(form: FormData, values: FlowDraftInput, locale: RequestLocale) -> list[Field]:
    """Return the name inputs, Dutch and English."""
    _ = locale.gettext
    return [
        _text_field(
            form,
            "label_nl",
            _("admin.flows.label_nl"),
            values.labels.get("nl", ""),
            _("admin.flows.name_hint"),
        ),
        _text_field(form, "label_en", _("admin.flows.label_en"), values.labels.get("en", "")),
    ]


def code_field(form: FormData, locale: RequestLocale, code: str | None) -> Field:
    """Return the code input: typed for a new flow, shown read-only for a stored one."""
    _ = locale.gettext
    if code is not None:
        return Field(
            "flow_code",
            _("admin.flows.code"),
            value=code,
            readonly=True,
            hint=_("admin.flows.code_fixed"),
        )
    return Field(
        "flow_code",
        _("admin.flows.code"),
        value=form.text("flow_code"),
        hint=_("admin.flows.code_hint"),
        error=form.error_for("flow_code"),
        attributes={"maxlength": "40", "spellcheck": "false", "autocapitalize": "none"},
    )


def duration_field(form: FormData, values: FlowDraftInput, locale: RequestLocale) -> Field:
    """Return the expected duration input; its bounds are the domain's own."""
    _ = locale.gettext
    stored = "" if values.duration_minutes is None else str(values.duration_minutes)
    return Field(
        "duration_minutes",
        _("admin.flows.duration"),
        "number",
        form.values.get("duration_minutes", stored) if form.values else stored,
        _(
            "admin.flows.duration_hint",
            minimum=MIN_SLOT_MINUTES,
            maximum=MAX_SLOT_MINUTES,
            step=SLOT_STEP_MINUTES,
        ),
        form.error_for("duration_minutes"),
        required=False,
        attributes={
            "min": str(MIN_SLOT_MINUTES),
            "max": str(MAX_SLOT_MINUTES),
            "step": str(SLOT_STEP_MINUTES),
            "inputmode": "numeric",
        },
    )


def kind_views(values: FlowDraftInput, locale: RequestLocale) -> list[KindView]:
    """Return the contact channels of the radio group, the chosen one checked."""
    _ = locale.gettext
    return [
        KindView(k.value, _(KIND_LABELS[k]), _(KIND_HINTS[k]), values.interaction_kind is k)
        for k in InteractionKind
    ]


def _suffixed(label: str, built: bool, locale: RequestLocale) -> str:
    """Return ``label``, with "to follow" added for something not built yet."""
    return label if built else locale.gettext("admin.flows.to_follow", label=label)


def action_select(
    saved_kind: InteractionKind | None,
    values: FlowDraftInput,
    form: FormData,
    locale: RequestLocale,
) -> SelectView | None:
    """Return the select of the first action, only for a saved channel that allows a choice."""
    _ = locale.gettext
    if saved_kind is None:
        return None
    allowed = flows.COMBINATIONS[saved_kind].actions
    if len(allowed) < 2:  # noqa: PLR2004 - one action is no choice
        return None
    options = tuple(
        OptionView(
            a.value,
            _suffixed(_(ACTION_LABELS[a]), a in flows.BUILT_ACTIONS, locale),
            values.handling_action is a,
            a not in flows.BUILT_ACTIONS,
        )
        for a in HandlingAction
        if a in allowed
    )
    return SelectView(
        "handling_action",
        _("admin.flows.first_action"),
        options,
        error=form.error_for("handling_action"),
    )


def pool_select(
    pools: Sequence[CapacityPool], values: FlowDraftInput, form: FormData, locale: RequestLocale
) -> SelectView:
    """Return the select of the capacity pool, with an empty first option."""
    _ = locale.gettext
    chosen = values.capacity_pool_id
    options = [OptionView("", _("admin.flows.pool.none"), chosen is None)]
    for pool in pools:
        name = (
            pool.name
            if pool.archived_at is None
            else _("admin.flows.pool.archived", name=pool.name)
        )
        options.append(OptionView(str(pool.id), name, pool.id == chosen))
    return SelectView(
        "capacity_pool_id",
        _("admin.flows.pool"),
        tuple(options),
        _("admin.flows.pool_hint"),
        form.error_for("capacity_pool_id"),
    )


def dispatch_select(values: FlowDraftInput, form: FormData, locale: RequestLocale) -> SelectView:
    """Return the select of the dispatch; what is not built yet is listed but not choosable."""
    _ = locale.gettext
    options = tuple(
        OptionView(
            d.value,
            _suffixed(_(DISPATCH_LABELS[d]), d in flows.BUILT_DISPATCH, locale),
            values.dispatch is d,
            d not in flows.BUILT_DISPATCH,
        )
        for d in DispatchKind
    )
    return SelectView(
        "dispatch", _("admin.flows.dispatch"), options, error=form.error_for("dispatch")
    )


def completion_select(values: FlowDraftInput, form: FormData, locale: RequestLocale) -> SelectView:
    """Return the select of the completion; the legacy observation is not offered at all."""
    _ = locale.gettext
    options = tuple(
        OptionView(
            c.value,
            _suffixed(_(COMPLETION_LABELS[c]), c in flows.BUILT_COMPLETION, locale),
            values.completion is c,
            c not in flows.BUILT_COMPLETION,
        )
        for c in CompletionPolicy
        if c is not CompletionPolicy.LEGACY_VIDEO_OBSERVATION
    )
    return SelectView(
        "completion", _("admin.flows.completion"), options, error=form.error_for("completion")
    )


def intake_checks(values: FlowDraftInput, locale: RequestLocale) -> list[CheckView]:
    """Return the two entry paths as checkboxes."""
    _ = locale.gettext
    return [
        CheckView(
            "intake_appointment",
            _(INTAKE_LABELS[IntakeMode.APPOINTMENT]),
            IntakeMode.APPOINTMENT in values.intake_modes,
        ),
        CheckView(
            "intake_on_demand",
            _(INTAKE_LABELS[IntakeMode.ON_DEMAND]),
            IntakeMode.ON_DEMAND in values.intake_modes,
        ),
    ]


def default_checks(
    values: FlowDraftInput,
    offered: frozenset[IntakeMode] | None,
    fixed: bool,
    locale: RequestLocale,
) -> list[CheckView]:
    """Return the two default paths as checkboxes.

    ``offered`` are the entry paths the saved revision offers (``None`` for a new flow, where
    both can be ticked): a default is enabled only for one of them, and not at all once the flow
    has a published revision (``fixed``).
    """
    _ = locale.gettext
    return [
        CheckView(
            "default_appointment",
            _("admin.flows.default_appointment"),
            IntakeMode.APPOINTMENT in values.default_for,
            fixed or (offered is not None and IntakeMode.APPOINTMENT not in offered),
        ),
        CheckView(
            "default_on_demand",
            _("admin.flows.default_on_demand"),
            IntakeMode.ON_DEMAND in values.default_for,
            fixed or (offered is not None and IntakeMode.ON_DEMAND not in offered),
        ),
    ]


def required_choices(
    skills: Sequence[Skill], values: FlowDraftInput, locale: RequestLocale
) -> list[SkillChoice]:
    """Return the skills of the flow as checkboxes named ``skill-<id>``."""
    return choices(skills, values.required_skill_ids, locale.code)


def subject_views(
    choices_: FlowChoices, values: FlowDraftInput, locale: RequestLocale
) -> list[SubjectView]:
    """Return one row per subject of the service: offered, and the extra skills it needs."""
    views = []
    for subject in choices_.subjects:
        extras = values.subject_skill_ids.get(subject.code, frozenset())
        views.append(
            SubjectView(
                subject.code,
                subject.label(locale.code),
                offer_name(subject.code),
                subject.code in values.subject_skill_ids,
                tuple(
                    ExtraView(
                        s.id or 0,
                        extra_name(subject.code, s.id or 0),
                        skill_label(s, locale.code),
                        s.id in extras,
                        s.archived,
                    )
                    for s in choices_.skills
                    if s.id is not None
                ),
            )
        )
    return views


def summary_rows(
    revision: FlowRevision,
    default_for: frozenset[IntakeMode],
    pool: CapacityPool | None,
    skills: Mapping[int, Skill],
    subject_labels: Mapping[str, str],
    locale: RequestLocale,
) -> list[tuple[str, str]]:
    """Return a published revision as ``(term, value)`` rows for the read-only view."""
    _ = locale.gettext
    none = _("admin.flows.summary.none")

    def names(ids: frozenset[int]) -> str:
        found = sorted(skill_label(skills[i], locale.code) for i in ids if i in skills)
        return ", ".join(found) or none

    kind = revision.interaction_kind
    rows = [
        (_("admin.flows.label_nl"), revision.labels.get("nl", "")),
        (_("admin.flows.label_en"), revision.labels.get("en", "")),
        (
            _("admin.flows.intake"),
            ", ".join(_(INTAKE_LABELS[m]) for m in sorted(revision.intake_modes, key=str)) or none,
        ),
        (_("admin.flows.contact_channel"), _(KIND_LABELS[kind]) if kind else none),
        (
            _("admin.flows.duration"),
            _("admin.flows.summary.minutes", minutes=revision.duration_minutes)
            if revision.duration_minutes
            else none,
        ),
        (_("admin.flows.pool"), pool_name(pool, locale) or none),
        (_("admin.flows.required_skills"), names(revision.required_skill_ids)),
        (
            _("admin.flows.subjects"),
            "; ".join(
                f"{subject_labels.get(code, code)}" + (f" ({names(extra)})" if extra else "")
                for code, extra in sorted(revision.subject_skill_ids.items())
            )
            or none,
        ),
        (
            _("admin.flows.dispatch"),
            _(DISPATCH_LABELS[revision.dispatch]) if revision.dispatch else none,
        ),
        (
            _("admin.flows.completion"),
            _(COMPLETION_LABELS[revision.completion]) if revision.completion else none,
        ),
        (
            _("admin.flows.collects"),
            _(
                "admin.flows.collects_email"
                if revision.collects_email
                else "admin.flows.collects_name_only"
            ),
        ),
        (
            _("admin.flows.defaults"),
            ", ".join(_(INTAKE_LABELS[m]) for m in sorted(default_for, key=str)) or none,
        ),
    ]
    return rows
