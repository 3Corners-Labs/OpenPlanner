"""Reading the flow editor's form and mapping the refusals of the flow screens (ADR 0121 step 2d).

The editor is one form with plain inputs and no script. A checkbox that stands for a skill is
named ``skill-<id>`` (the helper of ``app.admin.skill_inputs``), so is the flow's own; an extra
skill of one subject is ``extra_<code>_<id>`` and "this subject is offered" is
``offer_<code>``. A subject code holds no underscore, so the names cannot be mistaken for one
another. Every id is one to nine ASCII digits, a malformed name is ignored like any other name,
and the ids of one set are capped before anything is looked up (``chosen_ids``).
"""

import re
from collections.abc import Sequence
from enum import StrEnum
from typing import NoReturn

from app.admin.forms import FormData, raise_refusal
from app.admin.skill_inputs import chosen_ids
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
from app.web.locale import RequestLocale
from app.web.protection import RejectedRequest

LIMIT_NOTICE = "flows-limit-notice"
"""Id of the notice that the limit of flows is reached; a refused create links to it."""

CHOICE_FIELD = "required_skills"
"""Id of the group of the flow's skill checkboxes, where an error about the choice links to."""

SUBJECTS_FIELD = "subjects"
"""Id of the table of subjects, where an error about a subject's skills links to."""

DEFAULT_FIELD = "default_appointment"
"""Id of the first default-path checkbox, where an error about the defaults links to."""

_ID = re.compile(r"[0-9]{1,9}")
_NUMBER = re.compile(r"[0-9]{1,6}")
_EXTRA = re.compile(r"extra_([a-z0-9][a-z0-9-]{0,39})_([0-9]{1,9})")


def offer_name(code: str) -> str:
    """Return the name of the checkbox that offers subject ``code`` in the flow."""
    return f"offer_{code}"


def extra_name(code: str, skill_id: int) -> str:
    """Return the name of the checkbox of the extra skill ``skill_id`` of subject ``code``."""
    return f"extra_{code}_{skill_id}"


def default_action(kind: InteractionKind | None) -> HandlingAction | None:
    """Return the first action of ``kind`` that is built; the editor needs no choice for it.

    A channel with one allowed action has no choice at all; for more, the first built one in
    the order of ``HandlingAction`` is the default, and ``None`` for a channel not chosen yet.
    """
    if kind is None:
        return None
    allowed = flows.COMBINATIONS[kind].actions
    for action in HandlingAction:
        if action in allowed and action in flows.BUILT_ACTIONS:
            return action
    return next(iter(sorted(allowed)), None)


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


def _number(form: FormData, name: str) -> int | None:
    """Return a whole number of at most six ASCII digits; none (an error) for anything else."""
    raw = form.text(name)
    if not raw:
        return None
    if _NUMBER.fullmatch(raw):
        return int(raw)
    form.errors.append(FormError(name, form._(_message(name))))
    return None


def _message(name: str) -> str:
    """Return the catalogue key of the message about a malformed input ``name``."""
    return (
        "admin.form.error.slot_minutes" if name == "duration_minutes" else "admin.form.error.choice"
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


def _subject_skills(
    form: FormData, subjects: Sequence[Subject], max_skills: int
) -> dict[str, frozenset[int]]:
    """Return the offered subjects with the extra skills ticked for each.

    The cap of ``chosen_ids`` is applied to every subject's set, and a name of an unknown or
    not offered subject is ignored.
    """
    ticked: dict[str, set[int]] = {}
    for name in form.values:
        found = _EXTRA.fullmatch(name)
        if found:
            ticked.setdefault(found.group(1), set()).add(int(found.group(2)))
    result: dict[str, frozenset[int]] = {}
    for subject in subjects:
        if not form.checked(offer_name(subject.code)):
            continue
        ids = ticked.get(subject.code, set())
        if len(ids) > 2 * max_skills:
            form.errors.append(FormError(SUBJECTS_FIELD, form._("admin.flows.error.choice")))
            ids = set()
        result[subject.code] = frozenset(ids)
    return result


def parse_draft(
    form: FormData, subjects: Sequence[Subject], max_skills: int, *, fixed: bool = False
) -> FlowDraftInput:
    """Read the editor's form; what is malformed becomes an error of ``form`` and a blank value.

    ``subjects`` are the service's subjects, ``max_skills`` the organisation's limit of skills
    (the cap of a set of ticked ids) and ``fixed`` whether the flow's default entry paths are
    fixed already (it has a published revision), in which case the form does not send them.
    """
    kind = _member(form, "interaction_kind", InteractionKind)
    action = _member(form, "handling_action", HandlingAction)
    allowed = flows.COMBINATIONS[kind].actions if kind is not None else frozenset()
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
    return FlowDraftInput(
        {"nl": form.text("label_nl"), "en": form.text("label_en")},
        modes,
        kind,
        _number(form, "duration_minutes"),
        _pool(form),
        required,
        _subject_skills(form, subjects, max_skills),
        _member(form, "dispatch", DispatchKind),
        action if action in allowed else default_action(kind),
        _member(form, "completion", CompletionPolicy),
        form.checked("collects_email"),
        frozenset() if fixed else defaults,
    )


def refuse(form: FormData, error: DomainError, locale: RequestLocale) -> None:
    """Add the form error for a refusal of the flow screens; raise the page-level ones.

    A refused permission is an ``AccessDenied``; an unknown flow, group or a legacy flow answers
    404 like an unknown address. A reference to a pool or skill that cannot be used (missing,
    of another organisation, archived) is an error of its input, the same words for each, so an
    id tells nothing.

    Raises:
        AccessDenied: For ``not_permitted``.
        RejectedRequest: 404 for an unknown flow or service.
    """
    _ = locale.gettext
    raise_refusal(error)
    field = error.field
    if error.code is ErrorCode.SLUG_TAKEN:
        form.errors.append(FormError("flow_code", _("admin.flows.error.code_taken")))
    elif field == LIMIT_FIELD:
        form.errors.append(FormError(None, _("admin.flows.error.limit")))
    elif error.code is ErrorCode.GROUP_NOT_FOUND or (
        field == "flow_code" and error.code is ErrorCode.CONFIGURATION_OBJECT_MISSING
    ):
        raise RejectedRequest(404)
    elif field == "draft":
        form.errors.append(FormError(None, _("admin.flows.error.no_draft")))
    elif field in ("state", "revision"):
        form.errors.append(FormError(None, _("admin.flows.error.state")))
    elif field in ("capacity_pool_id", "required_skill_ids", "subject_skill_ids"):
        target = {
            "capacity_pool_id": "capacity_pool_id",
            "required_skill_ids": CHOICE_FIELD,
            "subject_skill_ids": SUBJECTS_FIELD,
        }[field]
        form.errors.append(FormError(target, _("admin.form.error.flow_choice")))
    elif error.code is ErrorCode.CONFIGURATION_OBJECT_EXISTS:
        form.errors.append(FormError(DEFAULT_FIELD, _("admin.flows.error.default_taken")))
    else:
        form.refusal(error)


def raise_unknown(error: DomainError) -> NoReturn:
    """Turn a refusal of a page that has no form into its response, or re-raise it.

    Raises:
        AccessDenied: For ``not_permitted``.
        RejectedRequest: 404 for an unknown service or flow, and a legacy flow.
        DomainError: ``error`` itself for anything else (the portal answers it).
    """
    raise_refusal(error)
    if error.code is ErrorCode.GROUP_NOT_FOUND or (
        error.field == "flow_code" and error.code is ErrorCode.CONFIGURATION_OBJECT_MISSING
    ):
        raise RejectedRequest(404)
    raise error
