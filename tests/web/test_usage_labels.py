"""The words for the codes of the usage reports (ADR 0117 step 6)."""

from app.i18n import Translator, load_catalogues
from app.web.usage_labels import TABLES, duration_text, label

_ = Translator(load_catalogues()).bind("en")


def test_a_known_code_has_its_words() -> None:
    assert label(_, "dispatch", "contact_center")
    assert label(_, "dispatch", "contact_center") != "reports.dispatch.contact_center"


def test_an_unknown_code_is_shown_as_it_is() -> None:
    assert label(_, "locales", "fr") == "fr"


def test_a_bucket_below_zero_reads_as_the_mirrored_bucket_and_early() -> None:
    early = label(_, "buckets", "-2")

    assert label(_, "buckets", "1") in early
    assert early != label(_, "buckets", "1")


def test_a_failure_with_a_reason_keeps_the_reason_code() -> None:
    text = label(_, "failures", "task_failed.timeout")

    assert text.startswith(label(_, "failures", "task_failed"))
    assert text.endswith("timeout")


def test_every_code_table_has_a_key_for_each_code() -> None:
    for table, codes in TABLES.items():
        for code in codes:
            assert label(_, table, code) != codes[code], (table, code)


def test_a_duration_reads_in_the_unit_that_fits() -> None:
    assert duration_text(_, 45) == "45 s"
    assert duration_text(_, 600) == "10 min"
    assert duration_text(_, 3 * 3600) == "3 h"
    assert duration_text(_, 4 * 86400) == "4 days"


def test_a_signed_duration_says_early_or_late() -> None:
    assert duration_text(_, -600, signed=True) != duration_text(_, 600, signed=True)
    assert "10 min" in duration_text(_, -600, signed=True)
    assert duration_text(_, 0, signed=True) == "0 s"
