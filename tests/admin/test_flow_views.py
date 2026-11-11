"""The texts and the options of the flow screens (ADR 0121 step 2d).

No enum member ships without its words, and an option is available exactly when the ``BUILT_*``
constant of the domain says so, for every kind of option.
"""

from enum import StrEnum
from typing import Any

import pytest

from app.admin import flow_views as views
from app.admin.flow_inputs import (
    CHOICE_FIELD,
    FLOW_REFUSALS,
    SUBJECT_REFUSALS,
    SUBJECTS_FIELD,
    empty_draft,
)
from app.admin.forms import FormData
from app.domain import service_flows as flows
from app.domain.errors import DomainError, ErrorCode
from app.domain.service_flows import (
    CompletionPolicy,
    DispatchKind,
    FlowState,
    HandlingAction,
    IntakeMode,
    InteractionKind,
    ReadinessCheck,
    ReadinessItem,
    ReadinessPolicy,
    ReadinessReason,
)
from app.i18n import CATALOGUE_DIR, load_catalogues
from tests.support.domain import NOW
from tests.support.locales import REQUIRED_LOCALES as REQUIRED
from tests.support.locales import locale_of, nl_locale
from tests.support.service_flows import flow, revision

TABLES: list[tuple[type[StrEnum], dict[StrEnum, str]]] = [
    (ReadinessCheck, dict(views.CHECK_TITLES)),
    (ReadinessReason, dict(views.REASON_TEXTS)),
    (InteractionKind, dict(views.KIND_LABELS)),
    (IntakeMode, dict(views.INTAKE_LABELS)),
    (HandlingAction, dict(views.ACTION_LABELS)),
    (ReadinessPolicy, dict(views.READINESS_TEXTS)),
    (DispatchKind, dict(views.DISPATCH_LABELS)),
    (CompletionPolicy, dict(views.COMPLETION_LABELS)),
    (FlowState, dict(views.STATE_LABELS)),
]


@pytest.mark.parametrize(("members", "table"), TABLES, ids=[t[0].__name__ for t in TABLES])
def test_every_member_has_a_text_in_both_languages(
    members: type[StrEnum], table: dict[StrEnum, str]
) -> None:
    catalogues = load_catalogues(CATALOGUE_DIR)

    assert set(table) == set(members)
    for locale in REQUIRED:
        for member in members:
            assert catalogues[locale][table[member]], (locale, member)


def test_every_kind_has_a_one_line_description_in_both_languages() -> None:
    catalogues = load_catalogues(CATALOGUE_DIR)

    assert set(views.KIND_HINTS) == set(InteractionKind)
    for locale in REQUIRED:
        for kind in InteractionKind:
            assert catalogues[locale][views.KIND_HINTS[kind]], (locale, kind)


def test_the_state_follows_is_for_not_built_yet_and_missing_for_every_other_reason() -> None:
    items = [ReadinessItem(ReadinessCheck.DATA, False, reason) for reason in ReadinessReason]

    shown = views.readiness_view(items, nl_locale(), draft=True)

    for row, reason in zip(shown.rows, ReadinessReason, strict=True):
        follows = reason is ReadinessReason.NOT_BUILT_YET
        assert row.word == ("Volgt" if follows else "Ontbreekt"), reason
        assert row.missing is True
    assert shown.ready is False


def test_not_built_yet_says_which_part_follows_for_the_checks_that_can_fail_on_it() -> None:
    catalogues = load_catalogues(CATALOGUE_DIR)
    assert set(views.NOT_BUILT_TEXTS) == {
        ReadinessCheck.DISPATCH,
        ReadinessCheck.COMPLETION,
        ReadinessCheck.RETENTION,
    }
    for locale in REQUIRED:
        texts = {catalogues[locale][key] for key in views.NOT_BUILT_TEXTS.values()}
        assert len(texts) == len(views.NOT_BUILT_TEXTS), locale
    items = [ReadinessItem(check, False, ReadinessReason.NOT_BUILT_YET) for check in ReadinessCheck]

    rows = views.readiness_view(items, nl_locale(), draft=False).rows

    sentences = {row.title: row.sentence for row in rows}
    assert sentences["Afronden"] == "Deze manier van afronden volgt in een latere versie."
    assert (
        "Afhandeling via het contactcenter volgt in een latere versie."
        in sentences["Contactkanaal en afhandeling"]
    )
    assert sentences["Bewaren en verwijderen"].startswith(
        "Het automatisch afsluiten en verwijderen"
    )
    assert sentences["Capaciteit"] == "Dit onderdeel van OpenPlanner volgt in een latere versie."


@pytest.mark.parametrize(
    ("draft", "ready", "heading"),
    [
        (True, True, "Het concept is volledig"),
        (True, False, "Het concept is nog niet volledig"),
        (False, True, "De gepubliceerde revisie is klaar voor gebruik"),
        (False, False, "De gepubliceerde revisie is nog niet klaar voor gebruik"),
    ],
)
def test_the_heading_names_what_the_checklist_judges(
    draft: bool, ready: bool, heading: str
) -> None:
    items = [ReadinessItem(check, ready) for check in ReadinessCheck]

    assert views.readiness_view(items, nl_locale(), draft=draft).heading == heading


def available(options: Any, value: str) -> bool:
    """Return whether the option ``value`` is one that can be chosen, with "(volgt)" when not."""
    (option,) = [o for o in options if o.value == value]
    assert option.disabled == ("(volgt)" in option.label)
    return not option.disabled


# Each kind of option, the constant that decides it, one member, and the view that shows it.
OPTIONS: list[tuple[str, StrEnum, Any]] = [
    (
        "BUILT_KINDS",
        InteractionKind.VIDEO,
        lambda locale: views.kind_views(empty_draft(), locale),
    ),
    (
        "BUILT_DISPATCH",
        DispatchKind.CONTACT_CENTER,
        lambda locale: (
            views.dispatch_select(empty_draft(), FormData({}, locale.gettext), locale).options
        ),
    ),
    (
        "BUILT_COMPLETION",
        CompletionPolicy.PROVIDER_EVENT,
        lambda locale: (
            views.completion_select(empty_draft(), FormData({}, locale.gettext), locale).options
        ),
    ),
    (
        "BUILT_ACTIONS",
        HandlingAction.ASSIGN_WORKSTATION,
        lambda locale: (
            (
                views.action_select(
                    InteractionKind.IN_PERSON, empty_draft(), FormData({}, locale.gettext), locale
                )
                or pytest.fail("a channel with two actions has a choice")
            ).options
        ),
    ),
]


@pytest.mark.parametrize(("constant", "member", "show"), OPTIONS, ids=[o[0] for o in OPTIONS])
def test_an_option_becomes_available_when_its_built_constant_holds_it_and_for_no_other_reason(
    monkeypatch: pytest.MonkeyPatch, constant: str, member: StrEnum, show: Any
) -> None:
    locale = nl_locale()
    before = {o.value: o.disabled for o in show(locale)}

    assert before[member.value] is True
    monkeypatch.setattr(flows, constant, frozenset({*getattr(flows, constant), member}))
    after = {o.value: o.disabled for o in show(locale)}

    assert after == {**before, member.value: False}
    assert available(show(locale), member.value)


def test_video_is_marked_to_follow_like_every_other_unbuilt_option() -> None:
    kinds = {k.value: k for k in views.kind_views(empty_draft(), locale_of("en"))}

    assert kinds["video"].disabled is True
    assert kinds["video"].label == "Video call (to follow)"
    assert kinds["telephone"].disabled is False


def test_the_read_out_shows_one_action_never_the_list_of_the_allowed_ones() -> None:
    locale = nl_locale()

    saved = views.readout(InteractionKind.IN_PERSON, HandlingAction.VIEW_REQUEST, locale)
    fallback = views.readout(InteractionKind.IN_PERSON, None, locale)
    nothing = views.readout(None, None, locale)

    assert saved is not None
    assert saved.ready == "Gereed voor afhandeling: wanneer de deelnemer zich meldt"
    assert saved.action == "Eerste actie van de medewerker: het verzoek bekijken"
    assert fallback == saved
    assert nothing is None
    assert " / " not in saved.action
    assert saved.action.count("het verzoek bekijken") == 1


def test_every_confirmation_has_its_words_in_both_languages() -> None:
    catalogues = load_catalogues(CATALOGUE_DIR)

    assert set(views.CONFIRMS) == set(views.ConfirmKind)
    for locale in REQUIRED:
        for kind, spec in views.CONFIRMS.items():
            keys = (spec.heading, spec.button, spec.lead, spec.refused)
            for key in keys:
                assert catalogues[locale][key], (locale, kind, key)


@pytest.mark.parametrize(
    ("kind", "state", "has_current", "has_draft", "allowed"),
    [
        (views.ConfirmKind.DISCARD, FlowState.DRAFT, False, True, False),
        (views.ConfirmKind.DISCARD, FlowState.DRAFT, True, True, True),
        (views.ConfirmKind.ARCHIVE, FlowState.DRAFT, True, False, False),
        (views.ConfirmKind.ARCHIVE, FlowState.ACTIVE, True, True, True),
        (views.ConfirmKind.DELETE, FlowState.DRAFT, False, True, True),
        (views.ConfirmKind.DELETE, FlowState.ACTIVE, True, False, False),
    ],
)
def test_a_confirmation_is_allowed_exactly_when_the_lifecycle_says_so(
    kind: views.ConfirmKind, state: FlowState, has_current: bool, has_draft: bool, allowed: bool
) -> None:
    the_flow = flow(
        state=state,
        flow_id=1,
        current=revision(published_at=NOW) if has_current else None,
        draft=revision(revision=2) if has_draft else None,
    )

    view = views.confirm_view(kind, the_flow, flows.flow_actions(the_flow, None), nl_locale())

    assert view.allowed is allowed
    assert bool(view.effects) is (allowed and kind is not views.ConfirmKind.DELETE)


def test_the_confirmations_name_the_flow_as_the_list_shows_it_and_the_draft_by_its_own_name() -> (
    None
):
    published = revision(published_at=NOW, labels={"nl": "Oud", "en": "Old"})
    the_flow = flow(
        state=FlowState.DRAFT,
        flow_id=1,
        current=published,
        draft=revision(revision=2, labels={"nl": "Nieuw", "en": "New"}),
    )
    actions = flows.flow_actions(the_flow, None)
    names = {
        kind: views.confirm_view(kind, the_flow, actions, nl_locale()).name
        for kind in views.ConfirmKind
    }

    assert names == {
        views.ConfirmKind.DISCARD: "Nieuw",
        views.ConfirmKind.ARCHIVE: "Oud",
        views.ConfirmKind.DELETE: "Oud",
    }


def test_an_error_about_a_subjects_skills_links_to_the_checkboxes_of_the_subject_page() -> None:
    error = DomainError(ErrorCode.INVALID_CONFIGURATION, "subject_skill_ids")

    editor = next(r for r in FLOW_REFUSALS.rules if r.matches(error))
    page = next(r for r in SUBJECT_REFUSALS.rules if r.matches(error))

    assert editor.target == SUBJECTS_FIELD
    assert page.target == CHOICE_FIELD
