"""The shared CSV writer: format, injection guard, window and checksum (ADR 0040, ADR 0117)."""

import csv
import hashlib
import io
from datetime import timedelta

import pytest

from app.domain.csv_export import (
    BOM,
    ExportLimits,
    ExportRefused,
    checksum,
    export_window,
    safe_cell,
    to_csv,
)
from tests.support.domain import NOW


def test_the_file_is_utf8_with_a_bom_semicolons_and_crlf() -> None:
    document = to_csv(("a", "b"), [("x;y", 1), (None, "é")])

    assert document == BOM + 'a;b\r\n"x;y";1\r\n;é\r\n'
    assert list(csv.reader(io.StringIO(document[1:], newline=""), delimiter=";"))[1] == ["x;y", "1"]


@pytest.mark.parametrize("text", ["=1+1", "+1", "-1", "@A1", "\tx", "\rx"])
def test_a_cell_that_reads_as_a_formula_gets_a_leading_quote(text: str) -> None:
    assert to_csv(("c",), [(text,)]).splitlines()[1].lstrip('"').startswith("'")


def test_a_number_is_not_a_formula_and_a_negative_one_stays_a_number() -> None:
    assert (safe_cell(-3), safe_cell(3), safe_cell(1.5)) == ("-3", "3", "1.5")
    assert safe_cell("-3") == "'-3"


def test_the_checksum_is_the_sha256_of_the_bytes_that_are_sent() -> None:
    document = to_csv(("c",), [("é",)])

    assert checksum(document) == hashlib.sha256(document.encode("utf-8")).hexdigest()


def test_the_window_defaults_to_the_last_days_and_refuses_a_longer_one() -> None:
    limits = ExportLimits(max_rows=10, max_days=31)

    assert export_window(None, None, NOW, limits) == (NOW - timedelta(days=31), NOW)
    with pytest.raises(ExportRefused) as refused:
        export_window(NOW - timedelta(days=32), NOW, NOW, limits)
    assert refused.value.code == "too_many_days"
