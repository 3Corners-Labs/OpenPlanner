"""The texts of the flow screens (ADR 0121 step 2d): no enum member ships without its words."""

from enum import StrEnum

import pytest

from app.admin import flow_views as views
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
    translator = Translator(load_catalogues(CATALOGUE_DIR))
    locale = RequestLocale(translator.bind("nl"), remember=False, translator=translator)
    items = [ReadinessItem(ReadinessCheck.DATA, False, reason) for reason in ReadinessReason]

    shown = views.readiness_view(items, locale)

    for row, reason in zip(shown.rows, ReadinessReason, strict=True):
        follows = reason is ReadinessReason.NOT_BUILT_YET
        assert row.word == ("Volgt" if follows else "Ontbreekt"), reason
        assert row.missing is True
    assert shown.ready is False
