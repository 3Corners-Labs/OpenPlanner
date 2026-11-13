"""What the screens of the flows show of a flow (ADR 0121 step 2d).

View models, form fields and the texts of the closed lists (intake modes, contact channels,
handling actions, dispatch, completion, the six readiness checks and their fourteen reasons).
Every text is a literal catalogue key in one of the tables below, so the catalogue tests see it
and a test fails for a member of an enum without text. Nothing here decides anything: the
readiness checklist and the table of combinations are the domain's, the views only say them.
"""

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.admin.flow_inputs import offer_name
from app.admin.forms import Field, FormData
from app.admin.skill_inputs import SkillChoice, choices, skill_name
from app.domain import flow_readiness as readiness_rules
from app.domain import service_flows as flows
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.flow_readiness import (
    FlowActions,
    ReadinessCheck,
    ReadinessItem,
    ReadinessReason,
    ReadinessState,
)
from app.domain.flow_service import FlowChoices, FlowDraftInput, FlowLine, FlowPage
from app.domain.scheduling import MAX_SLOT_MINUTES, MIN_SLOT_MINUTES, SLOT_STEP_MINUTES
from app.domain.service_flows import (
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    FlowState,
    HandlingAction,
    IntakeMode,
    InteractionKind,
    ReadinessPolicy,
    ServiceFlow,
)
from app.domain.skills import Skill
from app.web.locale import RequestLocale

INTAKE_LABELS: Mapping[IntakeMode, str] = {
    IntakeMode.APPOINTMENT: "admin.flows.intake.appointment",
    IntakeMode.ON_DEMAND: "admin.flows.intake.on_demand",
}
"""The words of the entry paths."""

MIN_CHOICE = 2
"""A contact channel with fewer allowed actions has no choice of action to show."""

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
    ReadinessCheck.ROLLBACK: "admin.flows.check.rollback",
}
"""The title of each readiness check."""

HEADINGS: Mapping[tuple[bool, ReadinessState], str] = {
    (True, ReadinessState.COMPLETE): "admin.flows.ready_heading.draft_ready",
    (True, ReadinessState.COMPLETE_EXCEPT_FOLLOWING): "admin.flows.ready_heading.draft_follows",
    (True, ReadinessState.INCOMPLETE): "admin.flows.ready_heading.draft_not_ready",
    (False, ReadinessState.COMPLETE): "admin.flows.ready_heading.published_ready",
    (False, ReadinessState.COMPLETE_EXCEPT_FOLLOWING): (
        "admin.flows.ready_heading.published_follows"
    ),
    (False, ReadinessState.INCOMPLETE): "admin.flows.ready_heading.published_not_ready",
}
"""The heading of the checklist by what it judges (the draft, or not) and where it stands."""

NOT_BUILT_TEXTS: Mapping[ReadinessCheck, str] = {
    ReadinessCheck.DISPATCH: "admin.flows.reason.not_built_yet.dispatch",
    ReadinessCheck.COMPLETION: "admin.flows.reason.not_built_yet.completion",
    ReadinessCheck.RETENTION: "admin.flows.reason.not_built_yet.retention",
}
"""The sentence of ``not_built_yet`` for the checks that can fail on it, saying which part
follows; any other check says it in general (``REASON_TEXTS``)."""

REASON_TEXTS: Mapping[ReadinessReason, str] = {
    ReadinessReason.LABELS_INVALID: "admin.flows.reason.labels_invalid",
    ReadinessReason.INTAKE_MISSING: "admin.flows.reason.intake_missing",
    ReadinessReason.POOL_MISSING: "admin.flows.reason.pool_missing",
    ReadinessReason.POOL_ARCHIVED: "admin.flows.reason.pool_archived",
    ReadinessReason.DURATION: "admin.flows.reason.duration",
    ReadinessReason.POOL_DOES_NOT_GUARANTEE: "admin.flows.reason.pool_does_not_guarantee",
    ReadinessReason.SKILL_UNMAPPED: "admin.flows.reason.skill_unmapped",
    ReadinessReason.SKILLS_NOT_CARRIED: "admin.flows.reason.skills_not_carried",
    ReadinessReason.SUBJECT_UNKNOWN: "admin.flows.reason.subject_unknown",
    ReadinessReason.KIND_MISSING: "admin.flows.reason.kind_missing",
    ReadinessReason.DISPATCH_MISSING: "admin.flows.reason.dispatch_missing",
    ReadinessReason.ACTION_NOT_ALLOWED: "admin.flows.reason.action_not_allowed",
    ReadinessReason.CONTACT_CENTER_MISSING: "admin.flows.reason.contact_center_missing",
    ReadinessReason.INTERACTION_NOT_CARRIED: "admin.flows.reason.interaction_not_carried",
    ReadinessReason.LOCATION_MISSING: "admin.flows.reason.location_missing",
    ReadinessReason.NOT_BUILT_YET: "admin.flows.reason.not_built_yet",
    ReadinessReason.COMPLETION_MISSING: "admin.flows.reason.completion_missing",
    ReadinessReason.COMPLETION_LEGACY_ONLY: "admin.flows.reason.completion_legacy_only",
    ReadinessReason.COMPLETION_NOT_FOR_DISPATCH: "admin.flows.reason.completion_not_for_dispatch",
    ReadinessReason.ROLLBACK_NOT_SAFE: "admin.flows.reason.rollback_not_safe",
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
    disabled: bool = False


@dataclass(frozen=True)
class SubjectView:
    """One subject of the service in the table of the editor.

    Attributes:
        code: The subject's code.
        label: Its name in the admin's language.
        offer_name: The name of its "offered" checkbox.
        offered: Whether the shown values offer it.
        extras: The extra skills the saved draft requires for it, by name; empty for none.
        link: The address of the page that chooses them, only for a subject the saved draft
            offers (none otherwise: what is not saved cannot be edited yet).
    """

    code: str
    label: str
    offer_name: str
    offered: bool
    extras: tuple[str, ...]
    link: str | None


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
    skills: str = ""


@dataclass(frozen=True)
class ReadinessView:
    """The readiness checklist as the aside shows it."""

    heading: str
    summary: str
    ready: bool
    follows_note: str
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
    modes = sorted(revision.intake_modes)
    return FlowRowView(
        flow.code,
        _label(revision.labels, locale.code) or flow.code,
        _label(revision.labels, other)
        if _label(revision.labels, other) != _label(revision.labels, locale.code)
        else "",
        ", ".join(_(INTAKE_LABELS[m]) for m in modes),
        _(KIND_LABELS[kind]) if kind is not None else "",
        pool_name(line.pool, locale),
        tuple(skill_name(s, locale.code) for s in line.skills),
        _(STATE_LABELS[flow.state]),
        flow.in_progress,
        flow.legacy,
        flow.state is FlowState.ARCHIVED,
    )


def readiness_view(
    items: Sequence[ReadinessItem],
    locale: RequestLocale,
    *,
    draft: bool,
    missing_skills: Sequence[str] = (),
) -> ReadinessView:
    """Translate the readiness checklist: a title, a state as a word and a sentence per item.

    ``draft`` says what the checklist judges: the draft, or the published revision. The heading
    names it, and a point that cannot be met yet says which part follows. ``missing_skills`` are
    the names of the skills the pool does not guarantee, listed under the skills point.
    """
    _ = locale.gettext
    rows = []
    for item in items:
        reason = item.reason
        if item.ok or reason is None:
            word, sentence, missing = _("admin.flows.ready_word.ok"), "", False
        else:
            follows = reason in readiness_rules.FOLLOWING
            word = _(
                "admin.flows.ready_word.follows" if follows else "admin.flows.ready_word.missing"
            )
            key = REASON_TEXTS[reason]
            built_later = reason is ReadinessReason.NOT_BUILT_YET
            sentence, missing = _(NOT_BUILT_TEXTS.get(item.code, key) if built_later else key), True
        lacking = (
            _("admin.flows.reason.missing_skills", skills=", ".join(missing_skills))
            if reason is ReadinessReason.POOL_DOES_NOT_GUARANTEE and missing_skills
            else ""
        )
        rows.append(ReadinessRow(_(CHECK_TITLES[item.code]), word, missing, sentence, lacking))
    done = sum(1 for item in items if item.ok)
    state = readiness_rules.readiness_state(items)
    return ReadinessView(
        _(HEADINGS[draft, state]),
        _("admin.flows.ready_count", done=done, total=len(items)),
        state is ReadinessState.COMPLETE,
        _("admin.flows.ready_follows_note", word=_("admin.flows.ready_word.follows"))
        if state is ReadinessState.COMPLETE_EXCEPT_FOLLOWING
        else "",
        tuple(rows),
    )


@dataclass(frozen=True)
class ActivationView:
    """What the page says about activating the flow: a button, or why there is none.

    Attributes:
        button: Whether activation is possible now, so the form with its button is shown.
        text: The sentence beside the button, or instead of it.
    """

    button: bool
    text: str


def activation_view(page: FlowPage, locale: RequestLocale) -> ActivationView | None:
    """Return the activation section of a flow that is not active yet; none for the others.

    There is no disabled button: when activation is not allowed the page says why, and when the
    reason is a part of OpenPlanner that follows later, it says that.
    """
    _ = locale.gettext
    if page.flow.state is not FlowState.DRAFT:
        return None
    if page.actions.activate:
        return ActivationView(True, _("admin.flows.activate_ready"))
    if page.activation is None:
        return ActivationView(False, _("admin.flows.activate_publish_first"))
    follows = any(item.reason in readiness_rules.FOLLOWING for item in page.activation)
    return ActivationView(
        False,
        _("admin.flows.activate_not_built" if follows else "admin.flows.activate_not_ready"),
    )


def publish_blocked(actions: FlowActions) -> bool:
    """Return whether there is a draft that cannot be published yet: the page says why."""
    return actions.save and not actions.publish


def actions_visible(actions: FlowActions, activation: ActivationView | None) -> bool:
    """Return whether the page has an actions section: something to do, or why something waits."""
    return actions.any or activation is not None or publish_blocked(actions)


@dataclass(frozen=True)
class ChecklistSection:
    """The readiness checklist of a page and what it judges.

    Attributes:
        view: The checklist as the aside shows it.
        basis: The sentence that says which revision it judges.
    """

    view: ReadinessView
    basis: str


def checklist_section(
    page: FlowPage | None, refused: Sequence[ReadinessItem] | None, locale: RequestLocale
) -> ChecklistSection | None:
    """Return the checklist section of a flow's page, none for a new flow.

    ``refused`` is the checklist of an activation that was refused: it is judged on the published
    revision, otherwise the page judges the draft when there is one.
    """
    _ = locale.gettext
    if page is None:
        return None
    judges_draft = refused is None and page.judges_draft
    items = page.items if refused is None else refused
    lacking = page.missing if refused is None else page.activation_missing
    names = sorted(skill_name(page.skills[i], locale.code) for i in lacking if i in page.skills)
    if judges_draft:
        basis = _("admin.flows.basis_draft", revision=page.flow.working.revision)
    else:
        shown = page.flow.current or page.flow.working
        basis = _("admin.flows.basis_published", revision=shown.revision)
    return ChecklistSection(
        readiness_view(items, locale, draft=judges_draft, missing_skills=names), basis
    )


class ConfirmKind(StrEnum):
    """The three changes of a flow that ask for a confirmation first."""

    DISCARD = "discard"
    ARCHIVE = "archive"
    DELETE = "delete"


@dataclass(frozen=True)
class ConfirmSpec:
    """The words and rules of one confirmation page, all as catalogue keys.

    Attributes:
        heading: The title.
        button: The button's text.
        lead: What the change does, shown when the lifecycle allows it.
        refused: What is said instead when the lifecycle does not allow it.
        effects: The list of what happens, from the flow.
        allowed: Whether the lifecycle allows the change: a field of ``FlowActions``.
        named: The revision whose name the page uses: what the list shows, or the draft.
    """

    heading: str
    button: str
    lead: str
    refused: str
    effects: Callable[[ServiceFlow], tuple[str, ...]]
    allowed: Callable[[FlowActions], bool]
    named: Callable[[ServiceFlow], FlowRevision]


def _archive_effects(flow: ServiceFlow) -> tuple[str, ...]:
    """Return what archiving does to ``flow``, a draft included only when there is one."""
    return (
        "admin.flows.archive_effect_new",
        "admin.flows.archive_effect_keeps",
        "admin.flows.archive_effect_listed",
        "admin.flows.archive_effect_final",
        *(("admin.flows.archive_effect_draft",) if flow.draft is not None else ()),
    )


CONFIRMS: Mapping[ConfirmKind, ConfirmSpec] = {
    ConfirmKind.DISCARD: ConfirmSpec(
        "admin.flows.discard_title",
        "admin.flows.discard_button",
        "admin.flows.discard_lead",
        "admin.flows.discard_none",
        lambda flow: (
            "admin.flows.discard_effect_draft",
            "admin.flows.discard_effect_published",
        ),
        lambda actions: actions.discard,
        lambda flow: flow.working,
    ),
    ConfirmKind.ARCHIVE: ConfirmSpec(
        "admin.flows.archive_title",
        "admin.flows.archive_button",
        "admin.flows.archive_lead",
        "admin.flows.archive_not_possible",
        _archive_effects,
        lambda actions: actions.archive,
        lambda flow: flow.listed,
    ),
    ConfirmKind.DELETE: ConfirmSpec(
        "admin.flows.delete_title",
        "admin.flows.delete_button",
        "admin.flows.delete_lead",
        "admin.flows.delete_not_possible",
        lambda flow: (),
        lambda actions: actions.delete,
        lambda flow: flow.listed,
    ),
}
"""One entry per confirmation: the table is the only place that knows which words go with which."""


@dataclass(frozen=True)
class ConfirmView:
    """A confirmation page as the template shows it."""

    heading: str
    lead: str
    effects: tuple[str, ...]
    button: str
    allowed: bool
    name: str


def confirm_view(
    kind: ConfirmKind, flow: ServiceFlow, actions: FlowActions, locale: RequestLocale
) -> ConfirmView:
    """Return the confirmation page of ``kind`` for ``flow``: words, effects and permission.

    The archive and delete pages name the flow by the name the list shows; the discard page by the
    name of the draft that goes.
    """
    _ = locale.gettext
    spec = CONFIRMS[kind]
    allowed = spec.allowed(actions)
    name = _label(spec.named(flow).labels, locale.code) or flow.code
    return ConfirmView(
        _(spec.heading),
        _(spec.lead if allowed else spec.refused),
        tuple(_(key) for key in spec.effects(flow)) if allowed else (),
        _(spec.button),
        allowed,
        name,
    )


def readout(
    kind: InteractionKind | None, action: HandlingAction | None, locale: RequestLocale
) -> ReadoutView | None:
    """Return what the saved contact channel means, none for a channel not saved yet.

    The readiness policy is the channel's row of the closed table; the action is the saved one,
    or the channel's default when none is saved: always one action, never a list of them.
    """
    _ = locale.gettext
    if kind is None:
        return None
    shown = action if action is not None else flows.default_action(kind)
    return ReadoutView(
        _(
            "admin.flows.readout.ready",
            policy=_(READINESS_TEXTS[flows.COMBINATIONS[kind].readiness]),
        ),
        _("admin.flows.readout.action", action=_(ACTION_LABELS[shown])) if shown else "",
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


def _available[M](
    member: M, built: Collection[M], label: str, locale: RequestLocale
) -> tuple[str, bool]:
    """Return the label of an option and whether it is disabled, from the ``BUILT_*`` constant.

    The one place that says which option can be chosen: what ``built`` (one of the ``BUILT_*``
    constants of ``service_flows``, read when called) does not hold is listed, marked "to follow"
    in words and disabled, so the screen widens with the constants and with nothing else.
    """
    if member in built:
        return label, False
    return locale.gettext("admin.flows.to_follow", label=label), True


def kind_views(values: FlowDraftInput, locale: RequestLocale) -> list[KindView]:
    """Return the contact channels of the radio group, the chosen one checked."""
    _ = locale.gettext
    views = []
    for kind in InteractionKind:
        label, disabled = _available(kind, flows.BUILT_KINDS, _(KIND_LABELS[kind]), locale)
        views.append(
            KindView(
                kind.value, label, _(KIND_HINTS[kind]), values.interaction_kind is kind, disabled
            )
        )
    return views


def action_select(
    saved_kind: InteractionKind | None,
    values: FlowDraftInput,
    form: FormData,
    locale: RequestLocale,
) -> SelectView | None:
    """Return the select of the first action, only for a saved channel that allows a choice."""
    _ = locale.gettext
    allowed = flows.choosable_actions(saved_kind)
    if len(allowed) < MIN_CHOICE:
        return None
    options = []
    for action in allowed:
        label, disabled = _available(action, flows.BUILT_ACTIONS, _(ACTION_LABELS[action]), locale)
        options.append(OptionView(action.value, label, values.handling_action is action, disabled))
    return SelectView(
        "handling_action",
        _("admin.flows.first_action"),
        tuple(options),
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
    options = []
    for dispatch in DispatchKind:
        label, disabled = _available(
            dispatch, flows.BUILT_DISPATCH, _(DISPATCH_LABELS[dispatch]), locale
        )
        options.append(OptionView(dispatch.value, label, values.dispatch is dispatch, disabled))
    return SelectView(
        "dispatch", _("admin.flows.dispatch"), tuple(options), error=form.error_for("dispatch")
    )


def completion_select(values: FlowDraftInput, form: FormData, locale: RequestLocale) -> SelectView:
    """Return the select of the completion; the legacy observation is not offered at all."""
    _ = locale.gettext
    options = []
    for completion in CompletionPolicy:
        if completion is CompletionPolicy.LEGACY_VIDEO_OBSERVATION:
            continue
        label, disabled = _available(
            completion, flows.BUILT_COMPLETION, _(COMPLETION_LABELS[completion]), locale
        )
        options.append(
            OptionView(completion.value, label, values.completion is completion, disabled)
        )
    return SelectView(
        "completion",
        _("admin.flows.completion"),
        tuple(options),
        error=form.error_for("completion"),
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
    page: FlowPage | None,
    choices_: FlowChoices,
    values: FlowDraftInput,
    locale: RequestLocale,
    base: str,
) -> list[SubjectView]:
    """Return one row per subject: offered, its extra skills and where to choose them.

    The extra skills are the saved draft's, never the form's: they are chosen on a page of their
    own (``base`` is the address of the flow), and only for a subject the saved draft offers.
    """
    saved = page.flow.draft.subject_skill_ids if page and page.flow.draft else {}
    named = page.skills if page else {}
    views = []
    for subject in choices_.subjects:
        extras = sorted(
            skill_name(named[i], locale.code) for i in saved.get(subject.code, ()) if i in named
        )
        views.append(
            SubjectView(
                subject.code,
                subject.label(locale.code),
                offer_name(subject.code),
                subject.code in values.subjects,
                tuple(extras),
                f"{base}/subjects/{subject.code}" if subject.code in saved else None,
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
        found = sorted(skill_name(skills[i], locale.code) for i in ids if i in skills)
        return ", ".join(found) or none

    kind = revision.interaction_kind
    rows = [
        (_("admin.flows.label_nl"), revision.labels.get("nl", "")),
        (_("admin.flows.label_en"), revision.labels.get("en", "")),
        (
            _("admin.flows.summary.intake"),
            ", ".join(_(INTAKE_LABELS[m]) for m in sorted(revision.intake_modes)) or none,
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
            ", ".join(_(INTAKE_LABELS[m]) for m in sorted(default_for)) or none,
        ),
    ]
    return rows
