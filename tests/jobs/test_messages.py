from dataclasses import replace

import pytest

from app.domain.booking import Booking, BookingStatus, Contact
from app.domain.job_plan import JobType
from app.domain.scheduling import PhoneMode
from app.domain.tokens import derive_token, new_token_salt
from app.i18n import Translator, load_catalogues
from app.jobs.messages import MessageComposer
from tests.support.domain import NOW, SLOT_0900, SLOT_0930, TEST_TOKEN_KEY, make_group

SALT = new_token_salt()
GROUP = make_group(name="Aangifte <Inkomstenbelasting>")
BOOKING = Booking(
    id=42,
    group_id=1,
    slot_id=1,
    start=SLOT_0900,
    end=SLOT_0930,
    status=BookingStatus.CONFIRMED,
    contact=Contact.create(
        name="Ada Lovelace", email="ada@example.org", phone=None, phone_mode=PhoneMode.OPTIONAL
    ),
    locale="nl",
    token_hash="h",
    created_at=NOW,
    token_salt=SALT,
)


@pytest.fixture(scope="module")
def composer() -> MessageComposer:
    return MessageComposer(
        Translator(load_catalogues()), TEST_TOKEN_KEY, "https://afspraak.example.nl/"
    )


def test_confirmation_in_dutch(composer: MessageComposer) -> None:
    message = composer.compose(JobType.SEND_CONFIRMATION, BOOKING, GROUP, NOW)

    url = f"https://afspraak.example.nl/b/{derive_token(TEST_TOKEN_KEY, SALT)}"
    assert message.recipient == "ada@example.org"
    assert message.subject == "Bevestiging van uw videogesprek op maandag 5 januari 2026"
    assert "Beste Ada," in message.text
    assert "maandag 5 januari 2026, 09:00–09:30" in message.text
    assert url in message.text
    assert f'href="{url}"' in message.html  # type: ignore[operator]
    assert "tot maandag 5 januari 2026, 08:55" in message.text
    assert "Lovelace" not in message.text  # first name only


def test_html_is_escaped_and_semantic(composer: MessageComposer) -> None:
    html = composer.compose(JobType.SEND_CONFIRMATION, BOOKING, GROUP, NOW).html or ""

    assert "Aangifte &lt;Inkomstenbelasting&gt;" in html
    assert "<Inkomstenbelasting>" not in html
    assert '<html lang="nl">' in html
    assert "<dl>" in html


def test_english_booking(composer: MessageComposer) -> None:
    message = composer.compose(JobType.SEND_REMINDER, replace(BOOKING, locale="en"), GROUP, NOW)

    assert message.subject.startswith("Reminder: video call on Monday, January 5, 2026 at")
    assert "Dear Ada," in message.text


def test_cancellation_has_no_manage_link_and_cancels_the_event(composer: MessageComposer) -> None:
    message = composer.compose(JobType.SEND_CANCELLATION, BOOKING, GROUP, NOW)

    assert "/b/" not in message.text
    assert "geannuleerd" in message.subject
    ics = message.attachments[0].data.decode()
    assert "METHOD:CANCEL" in ics
    assert "STATUS:CANCELLED" in ics


def test_calendar_attachment(composer: MessageComposer) -> None:
    attachment = composer.compose(JobType.SEND_CHANGE, BOOKING, GROUP, NOW).attachments[0]
    ics = attachment.data.decode()

    assert attachment.content_type == "text/calendar"
    assert "UID:booking-42@afspraak.example.nl" in ics
    assert "DTSTART:20260105T080000Z" in ics
    assert "DTEND:20260105T083000Z" in ics
    assert "Ada" not in ics
    assert "example.org" not in ics.replace("afspraak.example.nl", "")


def test_purged_booking_cannot_be_messaged(composer: MessageComposer) -> None:
    with pytest.raises(ValueError, match="contact"):
        composer.compose(JobType.SEND_CONFIRMATION, replace(BOOKING, contact=None), GROUP, NOW)


def test_only_message_jobs_can_be_composed(composer: MessageComposer) -> None:
    with pytest.raises(KeyError):
        composer.compose(JobType.PURGE_PII, BOOKING, GROUP, NOW)
