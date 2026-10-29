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
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from app.domain.booking import Booking, ChangeReason
from app.domain.channels import ChannelKind
from app.domain.closure import Closure
from app.domain.job_plan import JobType, change_deadline
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
    """

    subject_key: str
    intro_key: str
    includes_manage_link: bool
    cancels_event: bool
    sms_key: str | None = None
    manage_key: str = "message.manage"
    closure_notice: bool = False


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
}


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

        Raises:
            KeyError: If ``job_type`` is not a message job.
            ValueError: If the booking's personal data has been purged, it has no phone
                number for an SMS, the message has no SMS form, or a closure message has no
                closure.
        """
        kind = MESSAGE_KINDS[job_type]
        if booking.contact is None:
            raise ValueError("booking has no contact details")
        if kind.closure_notice and closure is None:
            raise ValueError("closure message without a closure")
        gettext = self._translator.bind(booking.locale, group.organisation_id)
        _ = _Texts(gettext, texts or {})
        if channel is ChannelKind.SMS:
            return self._sms(kind, booking, group, _)
        tz, locale = group.tz, booking.locale
        date = format_day(local_day(booking.start, tz), locale)
        start_time = format_clock(booking.start, tz, locale)
        end_time = format_clock(booking.end, tz, locale)
        deadline = change_deadline(booking.start, group.dispatch_lead)
        params = {"date": date, "time": start_time, "group_name": group.name}
        manage_url = self.manage_url(booking) if kind.includes_manage_link else None
        on_hold = kind.closure_notice
        join_url = (
            f"{manage_url}/join" if manage_url and group.video_enabled and not on_hold else None
        )
        context = {
            "locale": locale,
            "subject": _(kind.subject_key, **params),
            "greeting": _("message.greeting", first_name=booking.contact.first_name),
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
            "manage_text": _(
                kind.manage_key,
                deadline=f"{format_day(local_day(deadline, tz), locale)}, "
                f"{format_clock(deadline, tz, locale)}",
            ),
            "manage_link": _("message.manage_link"),
            "join_url": join_url,
            "join_text": _("message.join"),
            "join_link": _("message.join_link"),
            "signoff": _("message.signoff", group_name=group.name),
        }
        attachments: tuple[Attachment, ...] = ()
        if not on_hold:  # the appointment is on hold: leave the citizen's calendar alone
            attachments = (self._calendar(kind, booking, group, now, manage_url, _),)
        return OutboundMessage(
            channel=ChannelKind.EMAIL,
            recipient=booking.contact.email,
            subject=str(context["subject"]),
            text=self._environment.get_template("email/message.txt").render(context),
            html=self._environment.get_template("email/message.html").render(context),
            attachments=attachments,
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

    def _sms(self, kind: MessageKind, booking: Booking, group: Group, _: _Texts) -> OutboundMessage:
        """Return the SMS: one text block with first name, moment, team and the manage link."""
        contact = booking.contact
        if kind.sms_key is None or contact is None or contact.phone is None:
            raise ValueError("message has no SMS form or the booking no phone number")
        text = _(
            kind.sms_key,
            first_name=contact.first_name,
            date=format_day(local_day(booking.start, group.tz), booking.locale),
            time=format_clock(booking.start, group.tz, booking.locale),
            group_name=group.name,
            manage_url=self.manage_url(booking),
            reason=_reason(booking, _) or "",
        )
        return OutboundMessage(ChannelKind.SMS, contact.phone, "", text)
