"""The words a page uses for the delivery ledger (ADR 0124 point 10): every code has a text."""

from datetime import UTC, datetime

import pytest

from app.admin.delivery_status import FAULT_LABELS, OUTCOME_LABELS, DeliveryRow, delivery_view
from app.domain.inbound_gate import Refusal
from app.domain.webhook_ledger import Delivered, LedgerView
from app.domain.webhook_sources import ChainFault
from app.i18n import Translator, load_catalogues

AT = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)


def _moment(at: datetime) -> str:
    return at.strftime("%H:%M")


def test_every_outcome_reason_and_fault_has_a_text_in_both_languages() -> None:
    texts = load_catalogues()
    codes = {*(d.value for d in Delivered), *(r.value for r in Refusal)}

    assert set(OUTCOME_LABELS) == codes
    assert set(FAULT_LABELS) == set(ChainFault)
    for locale in ("nl", "en"):
        for key in (*OUTCOME_LABELS.values(), *FAULT_LABELS.values()):
            assert texts[locale][key], (locale, key)


@pytest.mark.parametrize("locale", ["nl", "en"])
def test_a_page_reads_the_ledger_in_words_in_a_fixed_order(locale: str) -> None:
    gettext = Translator(load_catalogues()).bind(locale)
    view = LedgerView(
        AT,
        {"stale": 2, "accepted": 5, "paused": 1},
        {ChainFault.NO_ADDRESS: 3},
        1,
        2,
        AT,
        AT,
        "stale",
    )

    shown = delivery_view(gettext, view, _moment)

    assert [row.count for row in shown.outcomes] == [5, 1, 2]  # accepted, paused, stale
    assert shown.faults == [DeliveryRow(gettext(FAULT_LABELS[ChainFault.NO_ADDRESS]), 3)]
    assert (shown.inside_list, shown.paused_dropped) == (1, 1)
    assert "09:00" in shown.last_accepted
    assert "09:00" in shown.last_refused
    assert gettext(OUTCOME_LABELS["stale"]) in shown.last_refused
    assert "stale" not in shown.last_refused.replace(gettext(OUTCOME_LABELS["stale"]), "")
