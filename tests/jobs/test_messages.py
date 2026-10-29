from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.booking import Booking, BookingStatus, ChangeReason, Contact
from app.domain.channels import ChannelKind
from app.domain.closure import Closure
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


def test_join_link_only_for_groups_with_video(composer: MessageComposer) -> None:
    video_group = replace(GROUP, video_host_email="host@example.nl")
    url = f"https://afspraak.example.nl/b/{derive_token(TEST_TOKEN_KEY, SALT)}/join"

    with_video = composer.compose(JobType.SEND_CONFIRMATION, BOOKING, video_group, NOW)
    without = composer.compose(JobType.SEND_CONFIRMATION, BOOKING, GROUP, NOW)
    cancelled = composer.compose(JobType.SEND_CANCELLATION, BOOKING, video_group, NOW)

    assert url in with_video.text
    assert f'href="{url}"' in (with_video.html or "")
    assert "15 minuten voor de start" in with_video.text
    assert "/join" not in without.text
    assert "/join" not in cancelled.text


SMS_BOOKING = replace(
    BOOKING,
    contact=Contact.create(
        name="Ada Lovelace",
        email="ada@example.org",
        phone="06 1234 5678",
        phone_mode=PhoneMode.OPTIONAL,
    ),
)


def test_sms_reminder_is_one_short_text_with_the_manage_link(composer: MessageComposer) -> None:
    message = composer.compose(
        JobType.SEND_REMINDER, SMS_BOOKING, GROUP, NOW, channel=ChannelKind.SMS
    )

    assert message.channel is ChannelKind.SMS
    assert message.recipient == "0612345678"
    assert message.html is None
    assert message.attachments == ()
    assert message.text.startswith("Beste Ada, herinnering: videogesprek met Aangifte")
    assert "maandag 5 januari 2026 om 09:00" in message.text
    assert f"/b/{derive_token(TEST_TOKEN_KEY, SALT)}" in message.text
    assert "Lovelace" not in message.text
    assert "ada@example.org" not in message.text


def test_sms_needs_a_phone_number_and_an_sms_form(composer: MessageComposer) -> None:
    with pytest.raises(ValueError, match="phone"):
        composer.compose(JobType.SEND_REMINDER, BOOKING, GROUP, NOW, channel=ChannelKind.SMS)
    with pytest.raises(ValueError, match="SMS form"):
        composer.compose(
            JobType.SEND_CONFIRMATION, SMS_BOOKING, GROUP, NOW, channel=ChannelKind.SMS
        )


def test_group_texts_replace_blocks_in_their_locale_only(composer: MessageComposer) -> None:
    texts = {
        ("nl", "message.reminder.subject"): "Denkt u aan {date}?",
        ("nl", "message.reminder.sms"): "Tot {date}, {first_name}! {manage_url}",
        ("en", "message.signoff"): "Kind regards, the {group_name} team",
    }

    email = composer.compose(JobType.SEND_REMINDER, BOOKING, GROUP, NOW, texts=texts)
    sms = composer.compose(
        JobType.SEND_REMINDER, SMS_BOOKING, GROUP, NOW, channel=ChannelKind.SMS, texts=texts
    )
    english = composer.compose(
        JobType.SEND_REMINDER, replace(BOOKING, locale="en"), GROUP, NOW, texts=texts
    )

    assert email.subject == "Denkt u aan maandag 5 januari 2026?"
    assert "Dit is een herinnering" in email.text  # blocks without a group text stay default
    assert sms.text.startswith("Tot maandag 5 januari 2026, Ada! https://afspraak.example.nl/b/")
    assert english.subject.startswith("Reminder:")
    assert "Kind regards, the Aangifte <Inkomstenbelasting> team" in english.text
    assert "the Aangifte &lt;Inkomstenbelasting&gt; team" in (english.html or "")


# --- emergency closure (ADR 0024) ----------------------------------------------------------

CLOSURE = Closure(
    1,
    1,
    SLOT_0900,
    None,
    {"nl": "Storing.\nWe bellen <niet>.", "en": "Outage."},
    NOW,
)


def test_suspension_carries_the_notice_and_no_join_link_or_calendar(
    composer: MessageComposer,
) -> None:
    booking = replace(BOOKING, status=BookingStatus.SUSPENDED, closure_id=1)
    video = replace(GROUP, video_host_email="host@example.org")

    message = composer.compose(JobType.SEND_SUSPENSION, booking, video, NOW, closure=CLOSURE)

    assert message.subject == "Uw videogesprek op maandag 5 januari 2026 kan niet doorgaan"
    assert "Toelichting:\nStoring.\nWe bellen <niet>." in message.text
    assert "<p>We bellen &lt;niet&gt;.</p>" in (message.html or "")
    assert "/join" not in message.text
    assert "kiest u een nieuwe tijd" in message.text
    assert message.attachments == ()


def test_closure_update_in_english(composer: MessageComposer) -> None:
    booking = replace(BOOKING, status=BookingStatus.SUSPENDED, closure_id=1, locale="en")

    message = composer.compose(JobType.SEND_CLOSURE_UPDATE, booking, GROUP, NOW, closure=CLOSURE)

    assert message.subject.startswith("News about your video call")
    assert "Outage." in message.text


def test_closure_message_needs_the_closure(composer: MessageComposer) -> None:
    with pytest.raises(ValueError, match="closure"):
        composer.compose(JobType.SEND_SUSPENSION, BOOKING, GROUP, NOW)


def test_reinstatement_has_the_calendar_and_no_notice(composer: MessageComposer) -> None:
    message = composer.compose(JobType.SEND_REINSTATEMENT, BOOKING, GROUP, NOW, closure=CLOSURE)

    assert "gaat toch door" in message.subject
    assert "Storing." not in message.text
    assert [a.filename for a in message.attachments] == ["afspraak.ics"]


def test_a_planners_cancellation_names_the_fixed_reason(composer: MessageComposer) -> None:
    cancelled = replace(BOOKING, change_reason=ChangeReason.STAFFING)

    message = composer.compose(JobType.SEND_CANCELLATION, cancelled, GROUP, NOW)

    reason = "Er zijn op dat moment niet genoeg medewerkers beschikbaar."
    assert f"Reden: {reason}" in message.text
    assert message.html is not None
    assert f"<strong>Reden:</strong> {reason}" in message.html


def test_a_cancellation_without_a_reason_has_no_reason_line(composer: MessageComposer) -> None:
    message = composer.compose(JobType.SEND_CANCELLATION, BOOKING, GROUP, NOW)

    assert "Reden" not in message.text


def test_a_planners_cancellation_by_sms_carries_the_reason_but_no_link(
    composer: MessageComposer,
) -> None:
    cancelled = replace(SMS_BOOKING, change_reason=ChangeReason.TECHNICAL)

    message = composer.compose(
        JobType.SEND_CANCELLATION, cancelled, GROUP, NOW, channel=ChannelKind.SMS
    )

    assert message.text.endswith("is geannuleerd. Er is een technische storing.")
    assert "/b/" not in message.text


def test_a_planners_move_by_sms_has_the_new_time_and_the_link(composer: MessageComposer) -> None:
    message = composer.compose(
        JobType.SEND_CHANGE, SMS_BOOKING, GROUP, NOW, channel=ChannelKind.SMS
    )

    assert "is verplaatst naar maandag 5 januari 2026 om 09:00" in message.text
    assert f"/b/{derive_token(TEST_TOKEN_KEY, SALT)}" in message.text


def _waiting(booking: Booking = BOOKING) -> Booking:
    return booking.request_rebook(ChangeReason.STAFFING, SLOT_0900 + timedelta(days=14))


def test_a_request_to_rebook_names_the_reason_and_the_deadline(
    composer: MessageComposer,
) -> None:
    message = composer.compose(JobType.SEND_REBOOK_REQUEST, _waiting(), GROUP, NOW)

    assert message.subject == "Kies een nieuwe tijd voor uw videogesprek"
    assert "Kiest u voor maandag 19 januari 2026, 09:00 geen nieuwe tijd" in message.text
    assert "Reden: Er zijn op dat moment niet genoeg medewerkers beschikbaar." in message.text
    assert f"/b/{derive_token(TEST_TOKEN_KEY, SALT)}" in message.text
    assert "/join" not in message.text
    assert "METHOD:CANCEL" in message.attachments[0].data.decode()


def test_the_reminder_to_rebook_has_no_calendar(composer: MessageComposer) -> None:
    message = composer.compose(JobType.SEND_REBOOK_REMINDER, _waiting(), GROUP, NOW)

    assert message.subject.startswith("Herinnering")
    assert message.attachments == ()


def test_a_lapsed_request_has_no_link(composer: MessageComposer) -> None:
    lapsed = _waiting().cancel(NOW)

    email = composer.compose(JobType.SEND_REBOOK_LAPSED, lapsed, GROUP, NOW)
    sms = composer.compose(
        JobType.SEND_REBOOK_LAPSED,
        _waiting(SMS_BOOKING).cancel(NOW),
        GROUP,
        NOW,
        channel=ChannelKind.SMS,
    )

    assert "is vervallen" in email.subject
    assert "/b/" not in email.text
    assert email.attachments == ()
    assert "/b/" not in sms.text


def test_the_sms_request_to_rebook_carries_reason_deadline_and_link(
    composer: MessageComposer,
) -> None:
    message = composer.compose(
        JobType.SEND_REBOOK_REQUEST, _waiting(SMS_BOOKING), GROUP, NOW, channel=ChannelKind.SMS
    )

    assert "niet genoeg medewerkers beschikbaar. Kies voor maandag 19 januari 2026" in message.text
    assert f"/b/{derive_token(TEST_TOKEN_KEY, SALT)}" in message.text
