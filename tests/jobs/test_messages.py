import os
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from app.adapters.db.text_overrides import CachedTextOverrides
from app.domain.booking import Booking, BookingStatus, ChangeReason, Contact
from app.domain.channels import ChannelKind
from app.domain.closure import Closure
from app.domain.contacts import ContactBlock, ContactKind, ContactOption
from app.domain.job_plan import JobType
from app.domain.scheduling import DispatchKind, PhoneMode
from app.domain.tokens import derive_token, new_token_salt
from app.i18n import Translator, load_catalogues
from app.jobs.messages import MessageComposer
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import two_organisations
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
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.org",
        phone=None,
        phone_mode=PhoneMode.OPTIONAL,
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
    assert "verplaatsen tot maandag 5 januari 2026, 08:00" in message.text
    assert "Annuleren kan tot het gesprek begint" in message.text
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
        first_name="Ada",
        last_name="Lovelace",
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
    assert message.recipient == "+31612345678"
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


# --- closure page and contact options in the mails (ADR 0037 §5, CL-5) -------------------

SNAPSHOTS = Path(__file__).parent / "snapshots"
CONTACTS = ContactBlock(
    (
        ContactOption.create(
            ContactKind.PHONE, "14 020", hours={"nl": "ma-vr 8-17", "en": "Mon-Fri 8-17"}
        ),
        ContactOption.create(ContactKind.WEBSITE, "https://gemeente.example/contact"),
        ContactOption.create(
            ContactKind.ADDRESS,
            "Stadhuisplein 1\n1234 AB Stad",
            labels={"nl": "Balie", "en": "Desk"},
        ),
    ),
    {"nl": "Zo bereikt u ons", "en": "How to reach us"},
)
CONTACT_CLOSURE = replace(CLOSURE, contacts=CONTACTS, headings={"nl": "Storing", "en": "Outage"})
FIXED = replace(BOOKING, status=BookingStatus.SUSPENDED, closure_id=1, token_salt="s" * 43)


@pytest.mark.parametrize(
    ("job_type", "locale"),
    [
        (JobType.SEND_SUSPENSION, "nl"),
        (JobType.SEND_SUSPENSION, "en"),
        (JobType.SEND_CLOSURE_UPDATE, "nl"),
        (JobType.SEND_CLOSURE_UPDATE, "en"),
    ],
)
def test_closure_mails_match_their_snapshots(
    composer: MessageComposer, job_type: JobType, locale: str
) -> None:
    group = replace(GROUP, slug="aangifte")
    message = composer.compose(
        job_type, replace(FIXED, locale=locale), group, NOW, closure=CONTACT_CLOSURE
    )

    # The closure image is never mailed; the only image is the product mark of the footer.
    assert (message.html or "").count("<img") == 1
    assert "/static/brand/mark-48.png" in (message.html or "")
    name = f"{job_type.value}.{locale}"
    for suffix, rendered in (("txt", message.text), ("html", message.html or "")):
        path = SNAPSHOTS / f"{name}.{suffix}"
        if os.environ.get("UPDATE_SNAPSHOTS"):
            path.write_text(rendered, encoding="utf-8")
        assert rendered == path.read_text(encoding="utf-8"), f"{path.name} changed"


def test_the_plain_text_part_has_the_contact_options_and_the_closure_page(
    composer: MessageComposer,
) -> None:
    group = replace(GROUP, slug="aangifte")

    message = composer.compose(JobType.SEND_SUSPENSION, FIXED, group, NOW, closure=CONTACT_CLOSURE)

    assert "Zo bereikt u ons:" in message.text
    assert "- Telefoon: 14 020 (ma-vr 8-17)" in message.text
    assert "- Website: https://gemeente.example/contact" in message.text
    assert "- Balie: Stadhuisplein 1, 1234 AB Stad" in message.text
    assert "https://afspraak.example.nl/g/aangifte/closure" in message.text
    html = message.html or ""
    assert '<a href="tel:14020">14 020</a>' in html
    assert '<a href="https://afspraak.example.nl/g/aangifte/closure">' in html
    assert "<h2>Storing</h2>" in html  # the closure's heading names the notice


def test_without_contact_options_the_mail_still_links_the_closure_page(
    composer: MessageComposer,
) -> None:
    message = composer.compose(JobType.SEND_CLOSURE_UPDATE, FIXED, GROUP, NOW, closure=CLOSURE)

    assert "Zo bereikt u ons" not in message.text
    assert f"/g/{GROUP.slug}/closure" in message.text
    assert "Toelichting:" in message.text


def test_other_mails_have_no_contact_block_or_closure_link(composer: MessageComposer) -> None:
    message = composer.compose(
        JobType.SEND_REINSTATEMENT, BOOKING, GROUP, NOW, closure=CONTACT_CLOSURE
    )

    assert "14 020" not in message.text
    assert "/closure" not in message.text


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


TEAM = make_group(
    slug="aangifte",
    name="Aangifte",
    dispatch_kind=DispatchKind.OPENPLANNER_QUEUE,
    calendar_invite_address="team-aangifte@example.org",
)


def test_team_invite_goes_to_the_team_with_first_name_reference_and_queue_link(
    composer: MessageComposer,
) -> None:
    message = composer.compose_team_invite(JobType.SEND_TEAM_INVITE, BOOKING, TEAM, NOW, "nl")
    ics = message.attachments[0].data.decode()

    assert message.recipient == "team-aangifte@example.org"
    assert "Ada" in message.text
    assert "OP-000042" in message.text
    assert "https://afspraak.example.nl/admin/groups/aangifte/queue" in message.text
    assert "Lovelace" not in message.text + ics
    assert "ada@example.org" not in message.text + str(message.html) + ics
    assert "/b/" not in message.text + ics  # no management or join link
    assert "METHOD:REQUEST" in ics
    assert message.attachments[0].content_type == "text/calendar"


def test_team_cancel_withdraws_the_same_event_without_the_name(
    composer: MessageComposer,
) -> None:
    invite = composer.compose_team_invite(JobType.SEND_TEAM_INVITE, BOOKING, TEAM, NOW, "en")
    cancel = composer.compose_team_invite(
        JobType.SEND_TEAM_CANCEL, BOOKING, TEAM, NOW + timedelta(minutes=1), "en"
    )
    uid = next(
        line
        for line in invite.attachments[0].data.decode().split("\r\n")
        if line.startswith("UID:")
    )

    assert uid in cancel.attachments[0].data.decode()
    assert "METHOD:CANCEL" in cancel.attachments[0].data.decode()
    assert "Ada" not in cancel.text
    assert "OP-000042" in cancel.text


def test_the_html_part_ends_with_the_product_footer_and_the_text_part_does_not(
    composer: MessageComposer,
) -> None:
    message = composer.compose(JobType.SEND_CONFIRMATION, BOOKING, GROUP, NOW)

    html = message.html or ""
    footer = html.split("<footer>", 1)[1].split("</footer>", 1)[0]
    assert "Afspraken via" in footer
    assert "<strong>OpenPlanner</strong>" in footer
    mark = "https://afspraak.example.nl/static/brand/mark-48.png"
    assert f'<img src="{mark}" alt="" width="24" height="24">' in footer
    assert html.index("Met vriendelijke groet") < html.index("<footer>")  # after the signoff
    assert "OpenPlanner" not in message.text  # the plain-text part is unchanged (ADR 0060 §6)
    assert "mark-48" not in message.text


def test_the_product_footer_follows_the_organisations_setting(composer: MessageComposer) -> None:
    message = composer.compose(
        JobType.SEND_CONFIRMATION, replace(BOOKING, locale="en"), GROUP, NOW, product_footer=False
    )

    assert "<footer>" not in (message.html or "")
    assert "OpenPlanner" not in (message.html or "") + message.text


def test_the_product_footer_speaks_the_participants_language(composer: MessageComposer) -> None:
    message = composer.compose(JobType.SEND_CONFIRMATION, replace(BOOKING, locale="en"), GROUP, NOW)

    assert "Appointments via" in (message.html or "")


PENDING = replace(
    BOOKING,
    status=BookingStatus.PENDING_CONFIRMATION,
    confirm_by=NOW + timedelta(hours=24),
    group_id=1,
)


@pytest.mark.parametrize("job_type", [JobType.SEND_CONFIRM_REQUEST, JobType.SEND_CONFIRM_REMINDER])
def test_a_request_to_confirm_links_the_confirm_page_and_names_nobody(
    composer: MessageComposer, job_type: JobType
) -> None:
    message = composer.compose(
        job_type, PENDING, replace(GROUP, video_host_email="host@example.nl"), NOW
    )
    confirm = f"https://afspraak.example.nl/b/{derive_token(TEST_TOKEN_KEY, SALT)}/confirm"

    # ADR 0056 §7: the address is not verified yet, so no name, join link or calendar file.
    assert "Ada" not in message.text
    assert "Goedendag," in message.text
    assert confirm in message.text
    assert f'href="{confirm}"' in (message.html or "")
    assert "/join" not in message.text
    assert message.attachments == ()
    assert "uiterlijk vrijdag 2 januari 2026, 09:00" in message.text


def test_the_request_to_confirm_in_english(composer: MessageComposer) -> None:
    message = composer.compose(
        JobType.SEND_CONFIRM_REQUEST, replace(PENDING, locale="en"), GROUP, NOW
    )

    assert message.subject == "Confirm your appointment with Aangifte <Inkomstenbelasting>"
    assert "Hello," in message.text


def test_a_lapsed_booking_links_the_group_page_to_book_again(composer: MessageComposer) -> None:
    lapsed = PENDING.expire(NOW + timedelta(hours=24))

    message = composer.compose(JobType.SEND_UNCONFIRMED_LAPSED, lapsed, GROUP, NOW)

    assert "/b/" not in message.text
    assert f"https://afspraak.example.nl/g/{GROUP.slug}" in message.text
    assert "vervallen" in message.subject
    assert message.attachments == ()


def test_the_confirmation_offers_a_trial_call_next_to_the_join_link(
    composer: MessageComposer,
) -> None:
    video = replace(GROUP, video_host_email="host@example.org")

    message = composer.compose(JobType.SEND_CONFIRMATION, BOOKING, video, NOW)

    token = derive_token(TEST_TOKEN_KEY, SALT)
    url = f"https://afspraak.example.nl/b/{token}/test"
    assert "Proefgesprek openen" in message.text
    assert url in message.text
    assert f'href="{url}"' in (message.html or "")


def test_no_trial_link_without_video_or_when_the_group_switched_it_off(
    composer: MessageComposer,
) -> None:
    no_video = composer.compose(JobType.SEND_CONFIRMATION, BOOKING, GROUP, NOW)
    off = composer.compose(
        JobType.SEND_CONFIRMATION,
        BOOKING,
        replace(GROUP, video_host_email="host@example.org", trial_call_enabled=False),
        NOW,
    )

    assert "/test" not in no_video.text
    assert "/test" not in off.text


async def _with_organisation_texts(
    texts: dict[tuple[str, str], str], organisation_id: int = 1
) -> MessageComposer:
    """Return a composer whose translator reads ``texts`` as the organisation's own."""
    uow = InMemoryUnitOfWork(two_organisations())
    uow.state.organisation_texts[organisation_id] = texts
    translator = Translator(load_catalogues(), CachedTextOverrides(uow))
    composer = MessageComposer(translator, TEST_TOKEN_KEY, "https://afspraak.example.nl/")
    await composer.refresh()
    return composer


async def test_an_organisation_text_replaces_the_catalogue_in_an_email_and_an_sms() -> None:
    composer = await _with_organisation_texts(
        {
            ("nl", "message.reminder.subject"): "Tot snel op {date}",
            ("nl", "message.reminder.sms"): "Tot {date}, {first_name}! {manage_url}",
        }
    )

    email = composer.compose(JobType.SEND_REMINDER, BOOKING, GROUP, NOW)
    sms = composer.compose(JobType.SEND_REMINDER, SMS_BOOKING, GROUP, NOW, channel=ChannelKind.SMS)
    english = composer.compose(JobType.SEND_REMINDER, replace(BOOKING, locale="en"), GROUP, NOW)

    assert email.subject == "Tot snel op maandag 5 januari 2026"
    assert "Dit is een herinnering" in email.text  # blocks without an organisation text
    assert sms.text.startswith("Tot maandag 5 januari 2026, Ada! https://afspraak.example.nl/b/")
    assert english.subject.startswith("Reminder:")  # the other locale keeps the catalogue


async def test_a_group_text_goes_before_the_organisation_text() -> None:
    composer = await _with_organisation_texts(
        {("nl", "message.reminder.subject"): "Organisatie {date}"}
    )
    group_texts = {("nl", "message.reminder.subject"): "Groep {date}"}

    own = composer.compose(JobType.SEND_REMINDER, BOOKING, GROUP, NOW, texts=group_texts)
    other = composer.compose(JobType.SEND_REMINDER, BOOKING, GROUP, NOW, texts={})

    assert own.subject == "Groep maandag 5 januari 2026"
    assert other.subject == "Organisatie maandag 5 januari 2026"


async def test_another_organisations_text_does_not_apply() -> None:
    composer = await _with_organisation_texts(
        {("nl", "message.reminder.subject"): "Andere organisatie"}, organisation_id=2
    )

    email = composer.compose(JobType.SEND_REMINDER, BOOKING, GROUP, NOW)

    assert email.subject.startswith("Herinnering:")


async def test_an_organisation_text_is_escaped_in_the_html_part() -> None:
    composer = await _with_organisation_texts(
        {("nl", "message.reminder.intro"): "<script>alert(1)</script> voor {group_name}"}
    )

    email = composer.compose(JobType.SEND_REMINDER, BOOKING, GROUP, NOW)

    assert "<script>" not in (email.html or "")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in (email.html or "")
    assert "<script>alert(1)</script>" in email.text  # the plain-text part is text


TERM_NL = {("nl", "term.agent"): "behandelaar", ("nl", "term.agents"): "behandelaars"}


async def test_the_organisation_term_fills_the_agent_placeholders_of_a_mail() -> None:
    composer = await _with_organisation_texts(TERM_NL)

    email = composer.compose(JobType.SEND_CONFIRMATION, BOOKING, GROUP, NOW)
    english = composer.compose(JobType.SEND_CONFIRMATION, replace(BOOKING, locale="en"), GROUP, NOW)

    assert "Een behandelaar neemt op het afgesproken moment contact met u op." in email.text
    assert "medewerker" not in email.text
    assert "One of our agents will contact you" in english.text  # another locale keeps its word


async def test_the_shipped_term_is_what_a_mail_said_before() -> None:
    composer = await _with_organisation_texts({})

    email = composer.compose(JobType.SEND_CONFIRMATION, BOOKING, GROUP, NOW)

    assert "Een medewerker neemt op het afgesproken moment contact met u op." in email.text


async def test_the_term_of_a_group_goes_before_the_term_of_its_organisation() -> None:
    uow = InMemoryUnitOfWork(two_organisations())
    uow.state.organisation_texts[1] = dict(TERM_NL)
    uow.state.group_texts[GROUP.id] = {
        ("nl", "term.agent"): "agent",
        ("nl", "term.agents"): "agenten",
    }
    composer = MessageComposer(
        Translator(load_catalogues(), CachedTextOverrides(uow)),
        TEST_TOKEN_KEY,
        "https://afspraak.example.nl/",
    )
    await composer.refresh()

    email = composer.compose(JobType.SEND_CONFIRMATION, BOOKING, GROUP, NOW)
    other = composer.compose(
        JobType.SEND_CONFIRMATION, BOOKING, replace(GROUP, id=GROUP.id + 1), NOW
    )

    assert "Een agent neemt" in email.text
    assert (
        "Een behandelaar neemt" in other.text
    )  # a group without its own word follows the organisation


async def test_a_group_text_may_use_the_agent_placeholders() -> None:
    composer = await _with_organisation_texts(TERM_NL)
    own = {("nl", "message.reminder.subject"): "{Agent} belt op {date}"}

    email = composer.compose(JobType.SEND_REMINDER, BOOKING, GROUP, NOW, texts=own)

    assert email.subject == "Behandelaar belt op maandag 5 januari 2026"
