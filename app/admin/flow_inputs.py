"""Reading the flow editor's form and mapping the refusals of the flow screens (ADR 0121 step 2d).

The editor is one form with plain inputs and no script. A checkbox that stands for a skill is
named ``skill-<id>`` (the helper of ``app.admin.skill_inputs``), and "this subject is offered" is
``offer_<code>``; a subject code holds no underscore, so the names cannot be mistaken for one
another. The extra skills of a subject are not in this form: they have a page of their own
(``SubjectSkillsPage``), so the form stays the same size however many subjects there are, and
saving it keeps what the draft already has for every subject that stays offered. Every id is one
to nine ASCII digits, a malformed name is ignored like any other name, and the ids of one set are
capped before anything is looked up (``chosen_ids``).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.admin.forms import FormData
from app.admin.skill_inputs import RefusalTable, Rule, chosen_ids
from app.domain import service_flows as flows
from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_service import LIMIT_FIELD, FlowDraftInput
from app.domain.routing import Subject
from app.domain.service_flows import (
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    HandlingAction,
    IntakeMode,
    InteractionKind,
)
from app.web.errors import FormError

LIMIT_NOTICE = "flows-limit-notice"
"""Id of the notice that the limit of flows is reached; a refused create links to it."""

CHOICE_FIELD = "required_skills"
"""Id of the group of the flow's skill checkboxes, where an error about the choice links to."""

SUBJECTS_FIELD = "subjects"
"""Id of the table of subjects, where an error about a subject's skills links to."""

DEFAULT_FIELD = "default_appointment"
"""Id of the first default-path checkbox, where an error about the defaults links to."""

_ID = re.compile(r"[0-9]{1,9}")


def offer_name(code: str) -> str:
    """Return the name of the checkbox that offers subject ``code`` in the flow."""
    return f"offer_{code}"


def from_revision(revision: FlowRevision, default_for: frozenset[IntakeMode]) -> FlowDraftInput:
    """Return what the editor shows of ``revision``: its stored values."""
    return FlowDraftInput(
        revision.labels,
        revision.intake_modes,
        revision.interaction_kind,
        revision.duration_minutes,
        revision.capacity_pool_id,
        revision.required_skill_ids,
        revision.subject_skill_ids,
        revision.dispatch,
        revision.handling_action,
        revision.completion,
        revision.collects_email,
        default_for,
    )


def empty_draft() -> FlowDraftInput:
    """Return the values of a new flow's form: appointment intake, own queue, operator completes."""
    return FlowDraftInput(
        {"nl": "", "en": ""},
        frozenset({IntakeMode.APPOINTMENT}),
        None,
        None,
        None,
        frozenset(),
        {},
        DispatchKind.OWN_QUEUE,
        None,
        CompletionPolicy.OPERATOR,
        True,
        frozenset(),
    )


def _member[E: StrEnum](form: FormData, name: str, kind: type[E]) -> E | None:
    """Return the member of ``kind`` submitted as ``name``; none (an error) for an unknown value."""
    raw = form.text(name)
    if not raw:
        return None
    for member in kind:
        if member.value == raw:
            return member
    form.errors.append(FormError(name, form._("admin.form.error.choice")))
    return None


def _pool(form: FormData) -> int | None:
    """Return the chosen pool's id, none (an error) for a malformed one."""
    raw = form.text("capacity_pool_id")
    if not raw:
        return None
    if _ID.fullmatch(raw):
        return int(raw)
    form.errors.append(FormError("capacity_pool_id", form._("admin.form.error.flow_choice")))
    return None


def _offered_subjects(
    form: FormData, subjects: Sequence[Subject], kept: Mapping[str, frozenset[int]]
) -> dict[str, frozenset[int]]:
    """Return the offered subjects, each with the extra skills the draft has for it already.

    A subject newly offered starts without extra skills, and one that is no longer offered is
    gone with its extra skills. The extra skills themselves are chosen on the subject's page.
    """
    return {
        subject.code: kept.get(subject.code, frozenset())
        for subject in subjects
        if form.checked(offer_name(subject.code))
    }


@dataclass(frozen=True)
class ParsedDraft:
    """What the editor's form says.

    Attributes:
        values: The draft it describes; what is malformed is a blank value and an error of the form.
        adjusted: Whether the posted first action did not fit the posted contact channel and
            was replaced by the channel's default: the page says so.
    """

    values: FlowDraftInput
    adjusted: bool


def parse_draft(
    form: FormData,
    subjects: Sequence[Subject],
    max_skills: int,
    *,
    fixed: bool = False,
    kept: Mapping[str, frozenset[int]] | None = None,
) -> ParsedDraft:
    """Read the editor's form; what is malformed becomes an error of ``form`` and a blank value.

    ``subjects`` are the service's subjects, ``max_skills`` the organisation's limit of skills
    (the cap of a set of ticked ids), ``fixed`` whether the flow's default entry paths are fixed
    already (it has a published revision), in which case the form does not send them, and
    ``kept`` the extra skills per subject the draft has now.
    """
    kind = _member(form, "interaction_kind", InteractionKind)
    action = _member(form, "handling_action", HandlingAction)
    fitting = action if action in flows.choosable_actions(kind) else flows.default_action(kind)
    try:
        required = chosen_ids(form, max_skills)
    except DomainError:
        form.errors.append(FormError(CHOICE_FIELD, form._("admin.flows.error.choice")))
        required = frozenset()
    modes = frozenset(
        mode
        for mode, name in (
            (IntakeMode.APPOINTMENT, "intake_appointment"),
            (IntakeMode.ON_DEMAND, "intake_on_demand"),
        )
        if form.checked(name)
    )
    defaults = frozenset(
        mode
        for mode, name in (
            (IntakeMode.APPOINTMENT, DEFAULT_FIELD),
            (IntakeMode.ON_DEMAND, "default_on_demand"),
        )
        if form.checked(name)
    )
    values = FlowDraftInput(
        {"nl": form.text("label_nl"), "en": form.text("label_en")},
        modes,
        kind,
        form.optional_integer("duration_minutes"),
        _pool(form),
        required,
        _offered_subjects(form, subjects, kept or {}),
        _member(form, "dispatch", DispatchKind),
        fitting,
        _member(form, "completion", CompletionPolicy),
        form.checked("collects_email"),
        frozenset() if fixed else defaults,
    )
    return ParsedDraft(values, action is not None and fitting != action)


FLOW_REFUSALS = RefusalTable(
    (
        Rule("flow_code", "admin.flows.error.code_taken", "flow_code", ErrorCode.SLUG_TAKEN),
        Rule(LIMIT_FIELD, "admin.flows.error.limit", None),
        Rule("draft", "admin.flows.error.no_draft", None),
        Rule("state", "admin.flows.error.state", None),
        Rule("revision", "admin.flows.error.state", None),
        Rule("readiness", "admin.flows.error.not_ready_publish", None),
        Rule("capacity_pool_id", "admin.form.error.flow_choice", "capacity_pool_id"),
        Rule("required_skill_ids", "admin.form.error.flow_choice", CHOICE_FIELD),
        Rule("subject_skill_ids", "admin.form.error.flow_choice", SUBJECTS_FIELD),
        Rule(
            None,
            "admin.flows.error.default_taken",
            DEFAULT_FIELD,
            ErrorCode.CONFIGURATION_OBJECT_EXISTS,
        ),
    ),
    lambda error: (
        error.code is ErrorCode.GROUP_NOT_FOUND
        or (
            error.code is ErrorCode.CONFIGURATION_OBJECT_MISSING
            and error.field in ("flow_code", "subject")
        )
    ),
)
"""The refusals of the flow screens. A pool or skill that cannot be used (missing, of another
organisation, a legacy pool, archived) is an error of its input in the same words for each, so an
id tells nothing; an unknown flow, service or subject and a legacy flow answer 404."""
