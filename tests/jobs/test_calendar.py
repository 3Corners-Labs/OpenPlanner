from app.jobs.calendar import build_ics
from tests.support.domain import NOW, SLOT_0900, SLOT_0930


def test_text_is_escaped_and_lines_are_folded() -> None:
    ics = build_ics(
        uid="booking-1@example.nl",
        start=SLOT_0900,
        end=SLOT_0930,
        summary="Gesprek; team, \\ afdeling\nnieuw",
        description="é" * 60,
        url=None,
        stamp=NOW,
    ).decode()

    assert r"SUMMARY:Gesprek\; team\, \\ afdeling\nnieuw" in ics
    assert "URL:" not in ics
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
    unfolded = ics.replace("\r\n ", "")
    assert "DESCRIPTION:" + "é" * 60 in unfolded
    assert ics.endswith("END:VCALENDAR\r\n")
