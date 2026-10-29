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


def test_a_request_names_organizer_and_attendee_and_carries_a_sequence() -> None:
    ics = build_ics(
        uid="booking-1-team@example.nl",
        start=SLOT_0900,
        end=SLOT_0930,
        summary="Videoafspraak",
        description="",
        url=None,
        stamp=NOW,
        invite="team@example.org",
        sequence=7,
    ).decode()
    cancel = build_ics(
        uid="booking-1-team@example.nl",
        start=SLOT_0900,
        end=SLOT_0930,
        summary="Videoafspraak",
        description="",
        url=None,
        stamp=NOW,
        cancelled=True,
        invite="team@example.org",
        sequence=8,
    ).decode()

    assert "METHOD:REQUEST" in ics
    assert "ORGANIZER:mailto:team@example.org" in ics
    assert "ATTENDEE;CUTYPE=GROUP;RSVP=FALSE:mailto:team@example.org" in ics
    assert "SEQUENCE:7" in ics
    assert "METHOD:CANCEL" in cancel
    assert "SEQUENCE:8" in cancel
