"""Compose the citizen's messages from editable text blocks (ADR 0009, ADR 0022).

Every email uses one fixed layout (HTML plus plain text); an SMS is one text block. Text
comes from the group's own texts first, then the i18n chain (organisation override,
catalogue). Placeholders are filled by the safe formatter in ``app.i18n``. A message carries
the first name, date, time, team and the management link; nothing else. Messages
about an emergency closure (ADR 0024) add the closure's notice: plain text an admin wrote,
escaped like every other value and never a template. Since ADR 0037 §5 they also carry the
closure's copy of the contact options as a fixed block and link to the closure page; the
closure's image is never mailed. A planner's cancellation (ADR 0044) adds the fixed reason
the planner chose, as a text block (``message.reason.<code>``); a planner's move or
cancellation also has an SMS form.

The request to confirm a new booking and its reminder (ADR 0056 §7) go to an address that is
not verified yet: they greet without a name, carry no calendar file or join link, and link to
the confirm page instead of the booking. The lapse message links to the group page to book
again.

A planner's proposal of a new time (ADR 0057) names the reason, the old and the proposed
time, links the confirm page and the page to choose another time, and removes the old time
from the citizen's calendar. When it is released unanswered, the message links the replaced
booking, which still awaits a new time.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from app.domain.booking import Booking, ChangeReason
from app.domain.channels import ChannelKind
from app.domain.closure import Closure
from app.domain.job_plan import JobType
from app.domain.message_texts import GroupTexts
from app.domain.ports import Attachment, OutboundMessage
from app.domain.scheduling import Group
from app.domain.tokens import derive_token
from app.i18n import BoundGettext, Translator, format_message
from app.i18n.dates import format_clock, format_day, local_day
from app.jobs.calendar import build_ics
from app.web.contact_labels import block_view
from app.web.templates import create_environment

TEMPLATE_DIR = Path(__file__).parent / "templates"


@dataclass(frozen=True)
class MessageKind:
    """Catalogue keys of one message type, written out so the catalogue test can see them.

    Attributes:
        subject_key: Subject line.
        intro_key: First paragraph.
        includes_manage_link: Whether the management link is in the message.
        cancels_event: Whether the calendar attachment removes the event.
        sms_key: The text of the SMS form, if the message has one.
        manage_key: Text above the management link.
        closure_notice: Whether the closure's notice is part of the message (the appointment
            is on hold: no join link and no calendar attachment either).
        rebook: A planner's request to choose a new time (ADR 0044): no join link, and the
            deadline in the texts is when the request lapses (``Booking.rebook_by``).
        calendar: Whether the email carries the calendar attachment.
        confirm: A request to confirm a new booking (ADR 0056): no name, link to the confirm
            page, and the deadline in the texts is ``Booking.confirm_by``.
        book_again: Whether the message links to the group page to make a new appointment.
        proposal: A planner's proposed new time (ADR 0057): the deadline is ``confirm_by``,
            the old time comes from the replaced booking, links go to confirm and to choose
            another time, and the calendar attachment removes the old time.
        released: A proposal released unanswered: the link and the deadline are those of the
            replaced booking, which still awaits a new time.
    """

    subject_key: str
    intro_key: str
    includes_manage_link: bool
    cancels_event: bool
    sms_key: str | None = None
    manage_key: str = "message.manage"
    closure_notice: bool = False
    rebook: bool = False
    calendar: bool = True
    confirm: bool = False
    book_again: bool = False
    proposal: bool = False
    released: bool = False


MESSAGE_KINDS: dict[JobType, MessageKind] = {
    JobType.SEND_CONFIRMATION: MessageKind(
        "message.confirmation.subject", "message.confirmation.intro", True, False
    ),
    JobType.SEND_CHANGE: MessageKind(
        "message.change.subject", "message.change.intro", True, False, "message.change.sms"
    ),
    JobType.SEND_REMINDER: MessageKind(
        "message.reminder.subject", "message.reminder.intro", True, False, "message.reminder.sms"
    ),
    JobType.SEND_CANCELLATION: MessageKind(
        "message.cancellation.subject",
        "message.cancellation.intro",
        False,
        True,
        "message.cancellation.sms",
    ),
    JobType.SEND_SUSPENSION: MessageKind(
        "message.suspension.subject",
        "message.suspension.intro",
        True,
        False,
        manage_key="message.manage_suspended",
        closure_notice=True,
    ),
    JobType.SEND_CLOSURE_UPDATE: MessageKind(
        "message.closure_update.subject",
        "message.closure_update.intro",
        True,
        False,
        manage_key="message.manage_suspended",
        closure_notice=True,
    ),
    JobType.SEND_REINSTATEMENT: MessageKind(
        "message.reinstatement.subject", "message.reinstatement.intro", True, False
    ),
    # ADR 0044: a planner asked the citizen to choose a new time; the old time is removed
    # from their calendar with the request, and the request lapses at ``rebook_by``.
    JobType.SEND_REBOOK_REQUEST: MessageKind(
        "message.rebook_request.subject",
        "message.rebook_request.intro",
        True,
        True,
        "message.rebook_request.sms",
        manage_key="message.manage_rebook",
        rebook=True,
    ),
    JobType.SEND_REBOOK_REMINDER: MessageKind(
        "message.rebook_reminder.subject",
        "message.rebook_reminder.intro",
        True,
        True,
        "message.rebook_reminder.sms",
        manage_key="message.manage_rebook",
        rebook=True,
        calendar=False,
    ),
    JobType.SEND_REBOOK_LAPSED: MessageKind(
        "message.rebook_lapsed.subject",
        "message.rebook_lapsed.intro",
        False,
        True,
        "message.rebook_lapsed.sms",
        rebook=True,
        calendar=False,
    ),
    # ADR 0056: confirm a new booking by email link; it lapses at ``confirm_by``.
    JobType.SEND_CONFIRM_REQUEST: MessageKind(
        "message.confirm_request.subject",
        "message.confirm_request.intro",
        True,
        False,
        manage_key="message.manage_confirm",
        calendar=False,
        confirm=True,
    ),
    JobType.SEND_CONFIRM_REMINDER: MessageKind(
        "message.confirm_reminder.subject",
        "message.confirm_reminder.intro",
        True,
        False,
        manage_key="message.manage_confirm",
        calendar=False,
        confirm=True,
    ),
    JobType.SEND_UNCONFIRMED_LAPSED: MessageKind(
        "message.unconfirmed_lapsed.subject",
        "message.unconfirmed_lapsed.intro",
        False,
        False,
        calendar=False,
        confirm=True,
        book_again=True,
    ),
    # ADR 0057: a planner proposed a new time; it is held until ``confirm_by``.
    JobType.SEND_PROPOSAL: MessageKind(
        "message.proposal.subject",
        "message.proposal.intro",
        True,
        True,
        "message.proposal.sms",
        manage_key="message.manage_proposal",
        proposal=True,
    ),
    JobType.SEND_PROPOSAL_REMINDER: MessageKind(
        "message.proposal_reminder.subject",
        "message.proposal_reminder.intro",
        True,
        False,
        "message.proposal_reminder.sms",
        manage_key="message.manage_proposal",
        calendar=False,
        proposal=True,
    ),
    JobType.SEND_PROPOSAL_RELEASED: MessageKind(
        "message.proposal_released.subject",
        "message.proposal_released.intro",
        True,
        False,
        "message.proposal_released.sms",
        manage_key="message.manage_rebook",
        calendar=False,
        released=True,
    ),
}


TEAM_TEXTS: dict[JobType, tuple[str, str]] = {
    JobType.SEND_TEAM_INVITE: ("message.team_invite.subject", "message.team_invite.intro"),
    JobType.SEND_TEAM_CANCEL: ("message.team_cancel.subject", "message.team_cancel.intro"),
}
"""Subject and first paragraph of the team's invite and its withdrawal (ADR 0043)."""


class _Texts:
    """``_()`` that prefers the group's own text of a block (ADR 0022)."""

    def __init__(self, gettext: BoundGettext, texts: GroupTexts) -> None:
        """Bind the locale's catalogue lookup and the group's texts."""
        self._ = gettext
        self._texts = texts

    def __call__(self, key: str, /, **params: object) -> str:
        """Return the group's text for ``key`` if it has one, else the i18n text."""
        own = self._texts.get((self._.locale, key))
        return format_message(own, params) if own is not None else self._(key, **params)


REASON_KEYS: dict[ChangeReason, str] = {
    ChangeReason.STAFFING: "message.reason.staffing",
    ChangeReason.TECHNICAL: "message.reason.technical",
    ChangeReason.UNFORESEEN: "message.reason.unforeseen",
}
"""The text block of each planner's reason (ADR 0044), written out for the catalogue test."""


def _deadline(
    kind: MessageKind, booking: Booking, group: Group, previous: Booking | None = None
) -> datetime:
    """Return the deadline the message names: a lapse of a request, else the change one."""
    if kind.released and previous is not None and previous.rebook_by is not None:
        return previous.rebook_by
    if kind.rebook and booking.rebook_by is not None:
        return booking.rebook_by
    if (kind.confirm or kind.proposal) and booking.confirm_by is not None:
        return booking.confirm_by
    return group.change_deadline(booking.start)


def _reason(booking: Booking, _: "_Texts") -> str | None:
    """Return the text of the planner's reason for ``booking``, if it has one."""
    return _(REASON_KEYS[booking.change_reason]) if booking.change_reason else None


class MessageComposer:
    """Builds the email or SMS for a message job.

    Args:
        translator: The application translator.
        token_key: Server key for re-deriving the management token (ADR 0011).
        public_base_url: Base URL of the booking pages, e.g. ``https://afspraak.example.nl``.
    """

    def __init__(self, translator: Translator, token_key: bytes, public_base_url: str) -> None:
        """Prepare the template environment."""
        self._translator = translator
        self._token_key = token_key
        self._base_url = public_base_url.rstrip("/")
        self._environment = create_environment([TEMPLATE_DIR])

    def manage_url(self, booking: Booking) -> str:
        """Return the citizen's management link for ``booking``."""
        return f"{self._base_url}/b/{derive_token(self._token_key, booking.token_salt)}"

    def compose(
        self,
        job_type: JobType,
        booking: Booking,
        group: Group,
        now: datetime,
        *,
        channel: ChannelKind = ChannelKind.EMAIL,
        texts: GroupTexts | None = None,
        closure: Closure | None = None,
        product_footer: bool = True,
        previous: Booking | None = None,
    ) -> OutboundMessage:
        """Return the message for ``job_type`` on ``channel``.

        Args:
            job_type: Which message.
            booking: The booking it is about (with its contact details).
            group: The booking's group.
            now: Stamp of the calendar entry.
            channel: Email or SMS (SMS exists for reminders).
            texts: The group's own texts; catalogue texts where it has none.
            closure: For closure messages: the closure whose notice they carry.
            product_footer: Whether the HTML part ends with "Appointments via OpenPlanner" and
                the product mark (ADR 0060 §6, the organisation's class D setting). The
                plain-text part never carries it.
            previous: For a proposal (ADR 0057): the booking it would replace, for the old
                time, its calendar entry and, once released, its link.

        Raises:
            KeyError: If ``job_type`` is not a message job.
            ValueError: If the booking's personal data has been purged, it has no phone
                number for an SMS, the message has no SMS form, a closure message has no
                closure, or a proposal message has no replaced booking.
        """
        kind = MESSAGE_KINDS[job_type]
        if booking.contact is None:
            raise ValueError("booking has no contact details")
        if kind.closure_notice and closure is None:
            raise ValueError("closure message without a closure")
        if (kind.proposal or kind.released) and previous is None:
            raise ValueError("proposal message without the booking it replaces")
        gettext = self._translator.bind(booking.locale, group.organisation_id)
        _ = _Texts(gettext, texts or {})
        if channel is ChannelKind.SMS:
            return self._sms(kind, booking, group, _, previous)
        tz, locale = group.tz, booking.locale
        date = format_day(local_day(booking.start, tz), locale)
        start_time = format_clock(booking.start, tz, locale)
        end_time = format_clock(booking.end, tz, locale)
        deadline = _deadline(kind, booking, group, previous)
        deadline_text = (
            f"{format_day(local_day(deadline, tz), locale)}, {format_clock(deadline, tz, locale)}"
        )
        params = {"date": date, "time": start_time, "group_name": group.name}
        if kind.rebook or kind.proposal or kind.released:
            params["deadline"] = deadline_text
        if previous is not None:
            params["old_date"] = format_day(local_day(previous.start, tz), locale)
            params["old_time"] = format_clock(previous.start, tz, locale)
        manage_url = self.manage_url(booking) if kind.includes_manage_link else None
        other_url = f"{manage_url}/reschedule" if manage_url and kind.proposal else None
        if manage_url and (kind.confirm or kind.proposal):  # the page confirms on its POST
            manage_url = f"{manage_url}/confirm"
        if kind.released and previous is not None:  # choose a time for the replaced booking
            manage_url = self.manage_url(previous)
        on_hold = kind.closure_notice
        join_url = (
            f"{manage_url}/join"
            if manage_url
            and group.video_enabled
            and not on_hold
            and not kind.rebook
            and not kind.confirm
            and not kind.proposal
            and not kind.released
            else None
        )
        greeting = (
            _("message.greeting_anonymous")  # ADR 0056 §7: the address is not verified yet
            if kind.confirm
            else _("message.greeting", first_name=booking.contact.first_name)
        )
        context = {
            "locale": locale,
            "subject": _(kind.subject_key, **params),
            "greeting": greeting,
            "intro": _(kind.intro_key, **params),
            "when_label": _("message.when"),
            "when": f"{date}, {start_time}–{end_time}",
            "team_label": _("message.team"),
            "group_name": group.name,
            "manage_url": manage_url,
            "notice_label": (closure.heading(locale) if closure else None)
            or _("message.closure_notice"),
            "notice": closure.notice(locale) if closure and on_hold else None,
            "contacts": block_view(closure.contacts, locale, gettext)
            if closure and on_hold
            else None,
            "closure_url": f"{self._base_url}/g/{group.slug}/closure" if on_hold else None,
            "closure_text": _("message.closure_page"),
            "closure_link": _("message.closure_page_link"),
            "reason_label": _("message.reason"),
            "reason": _reason(booking, _),
            "manage_text": _(kind.manage_key, deadline=deadline_text),
            "manage_link": _(
                "message.confirm_link" if kind.confirm or kind.proposal else "message.manage_link"
            ),
            "book_url": other_url
            or (f"{self._base_url}/g/{group.slug}" if kind.book_again else None),
            "book_text": _("message.proposal_other" if other_url else "message.book_again"),
            "book_link": _(
                "message.proposal_other_link" if other_url else "message.book_again_link"
            ),
            "join_url": join_url,
            "join_text": _("message.join"),
            "join_link": _("message.join_link"),
            "signoff": _("message.signoff", group_name=group.name),
            "product_footer": {
                "via": _("site.product_footer_via"),
                "name": _("site.product_name"),
                "mark_url": f"{self._base_url}/static/brand/mark-48.png",
            }
            if product_footer
            else None,
        }
        attachments: tuple[Attachment, ...] = ()
        if not on_hold and kind.calendar:  # on hold: leave the citizen's calendar alone
            # A proposal removes the old time; the confirmation brings the new one.
            entry = previous if kind.proposal and previous is not None else booking
            attachments = (self._calendar(kind, entry, group, now, manage_url, _),)
        return OutboundMessage(
            channel=ChannelKind.EMAIL,
            recipient=booking.contact.email,
            subject=str(context["subject"]),
            text=self._environment.get_template("email/message.txt").render(context),
            html=self._environment.get_template("email/message.html").render(context),
            attachments=attachments,
        )

    def compose_team_invite(
        self, job_type: JobType, booking: Booking, group: Group, now: datetime, locale: str
    ) -> OutboundMessage:
        """Return the calendar invite to an own-queue team, or its withdrawal (ADR 0043).

        Sent to ``group.calendar_invite_address`` in the organisation's ``locale``, in the same
        fixed layout as the citizen's mail. It carries the first name (the invite only), the
        booking reference, the time and a link to the group's queue: never the meeting link,
        which an agent gets by claiming, nor any other personal data.

        Raises:
            ValueError: If the group sends no invite or the booking has no contact details.
        """
        address = group.calendar_invite_address
        if not group.sends_team_invite or address is None or booking.contact is None:
            raise ValueError("no team invite for this booking")
        cancel = job_type is JobType.SEND_TEAM_CANCEL
        _ = self._translator.bind(locale, group.organisation_id)
        tz = group.tz
        date = format_day(local_day(booking.start, tz), locale)
        start_time = format_clock(booking.start, tz, locale)
        params = {
            "date": date,
            "time": start_time,
            "group_name": group.name,
            "reference": booking.reference,
            "first_name": booking.contact.first_name,
        }
        subject_key, intro_key = TEAM_TEXTS[job_type]
        queue_url = f"{self._base_url}/admin/groups/{group.slug}/queue"
        context = {
            "locale": locale,
            "subject": _(subject_key, **params),
            "greeting": _("message.team_invite.greeting"),
            "intro": _(intro_key, **params),
            "when_label": _("message.when"),
            "when": f"{date}, {start_time}–{format_clock(booking.end, tz, locale)}",
            "team_label": _("message.team"),
            "group_name": group.name,
            "notice": None,
            "contacts": None,
            "closure_url": None,
            "reason": None,
            "join_url": None,
            "book_url": None,
            "manage_url": None if cancel else queue_url,
            "manage_text": _("message.team_invite.queue"),
            "manage_link": _("message.team_invite.queue_link"),
            "signoff": _("message.team_invite.signoff"),
            # The team's own mailbox needs no "Appointments via OpenPlanner" (ADR 0060 §6).
            "product_footer": None,
        }
        calendar = build_ics(
            uid=f"booking-{booking.id}-team@{urlsplit(self._base_url).hostname or 'openplanner'}",
            start=booking.start,
            end=booking.end,
            summary=_("message.team_invite.summary", **params),
            description=str(context["intro"]),
            url=None if cancel else queue_url,
            stamp=now,
            cancelled=cancel,
            invite=address,
            sequence=int(now.timestamp()) // 60,
        )
        return OutboundMessage(
            channel=ChannelKind.EMAIL,
            recipient=address,
            subject=str(context["subject"]),
            text=self._environment.get_template("email/message.txt").render(context),
            html=self._environment.get_template("email/message.html").render(context),
            attachments=(Attachment("afspraak.ics", "text/calendar", calendar),),
        )

    def _calendar(
        self,
        kind: MessageKind,
        booking: Booking,
        group: Group,
        now: datetime,
        manage_url: str | None,
        _: "_Texts",
    ) -> Attachment:
        """Return the ``.ics`` attachment for ``booking``."""
        calendar = build_ics(
            uid=f"booking-{booking.id}@{urlsplit(self._base_url).hostname or 'openplanner'}",
            start=booking.start,
            end=booking.end,
            summary=_("message.calendar_summary", group_name=group.name),
            description=manage_url or "",
            url=manage_url,
            stamp=now,
            cancelled=kind.cancels_event,
        )
        return Attachment("afspraak.ics", "text/calendar", calendar)

    def _sms(
        self,
        kind: MessageKind,
        booking: Booking,
        group: Group,
        _: _Texts,
        previous: Booking | None = None,
    ) -> OutboundMessage:
        """Return the SMS: one text block with first name, moment, team and the manage link.

        A released proposal links the replaced booking, which still awaits a new time.
        """
        contact = booking.contact
        if kind.sms_key is None or contact is None or contact.phone is None:
            raise ValueError("message has no SMS form or the booking no phone number")
        text = _(
            kind.sms_key,
            first_name=contact.first_name,
            date=format_day(local_day(booking.start, group.tz), booking.locale),
            time=format_clock(booking.start, group.tz, booking.locale),
            group_name=group.name,
            manage_url=self.manage_url(previous if kind.released and previous else booking),
            reason=_reason(booking, _) or "",
            deadline=format_day(
                local_day(_deadline(kind, booking, group, previous), group.tz), booking.locale
            ),
        )
        return OutboundMessage(ChannelKind.SMS, contact.phone, "", text)
