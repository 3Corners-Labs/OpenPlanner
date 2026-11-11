"""The texts and the options of the flow screens (ADR 0121 step 2d).

No enum member ships without its words, and an option is available exactly when the ``BUILT_*``
constant of the domain says so, for every kind of option.
"""

from enum import StrEnum
from typing import Any

import pytest

from app.admin import flow_views as views
from app.admin.flow_inputs import empty_draft
from app.admin.forms import FormData
from app.domain import service_flows as flows
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
from app.i18n import CATALOGUE_DIR, Translator, load_catalogues
from app.web.locale import RequestLocale
from tests.i18n.test_catalogues import REQUIRED


def locale_of(code: str) -> RequestLocale:
    """Return the locale ``code`` as a request has it."""
    translator = Translator(load_catalogues(CATALOGUE_DIR))
    return RequestLocale(translator.bind(code), remember=False, translator=translator)


def nl_locale() -> RequestLocale:
    """Return the Dutch locale."""
    return locale_of("nl")


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
        "Dit contactkanaal of deze manier van afhandelen"
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
