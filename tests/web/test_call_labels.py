"""What an administrator reads after a failed contact-center task (ADR 0065, ADR 0123 step 7a)."""

import pytest

from app.domain.call_facts import FAILURE_CODES, failure_code
from app.domain.calls import CONVERSATION_OPEN, MESSAGING_NOT_CONFIGURED, EventKind
from app.i18n import CATALOGUE_DIR, load_catalogues
from app.web.call_labels import failure_hint
from tests.support.locales import REQUIRED_LOCALES


@pytest.mark.parametrize("code", [MESSAGING_NOT_CONFIGURED, CONVERSATION_OPEN])
def test_a_refused_conversation_has_its_own_hint(code: str) -> None:
    key = failure_hint(EventKind.TASK_FAILED, code)

    assert key is not None
    assert key not in {"call.hint.task.other", "call.hint.unavailable"}
    catalogues = load_catalogues(CATALOGUE_DIR)
    for locale in REQUIRED_LOCALES:
        assert catalogues[locale][key]


@pytest.mark.parametrize("code", [MESSAGING_NOT_CONFIGURED, CONVERSATION_OPEN])
def test_the_hint_says_that_no_work_item_was_made_instead(code: str) -> None:
    key = failure_hint(EventKind.TASK_FAILED, code)
    catalogues = load_catalogues(CATALOGUE_DIR)

    assert key is not None
    assert "work item" in catalogues["en"][key]
    assert "werkitem" in catalogues["nl"][key]


@pytest.mark.parametrize("code", [MESSAGING_NOT_CONFIGURED, CONVERSATION_OPEN])
def test_a_refused_conversation_is_a_failure_code_of_its_own_in_the_reports(code: str) -> None:
    assert code in FAILURE_CODES
    assert failure_code(code) == code
