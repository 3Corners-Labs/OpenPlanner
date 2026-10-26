"""Compose the citizen's messages from catalogue texts (ADR 0009).

Every message uses one fixed layout (HTML plus plain text); only text blocks come from the
i18n catalogues. Placeholders are filled by the safe formatter in ``app.i18n``. The message
carries the first name, date, time, team and the management link; nothing else. Messages
about an emergency closure (ADR 0022) add the closure's notice: plain text an admin wrote,
escaped like every other value and never a template.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from app.domain.booking import Booking
from app.domain.channels import ChannelKind
from app.domain.closure import Closure
from app.domain.job_plan import JobType, change_deadline
from app.domain.ports import Attachment, OutboundMessage
from app.domain.scheduling import Group
from app.domain.tokens import derive_token
from app.i18n import BoundGettext, Translator
from app.i18n.dates import format_clock, format_day, local_day
from app.jobs.calendar import build_ics
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
        manage_key: Text above the management link.
        closure_notice: Whether the closure's notice is part of the message (the appointment
            is on hold: no join link and no calendar attachment either).
    """

    subject_key: str
    intro_key: str
    includes_manage_link: bool
    cancels_event: bool
    manage_key: str = "message.manage"
    closure_notice: bool = False


MESSAGE_KINDS: dict[JobType, MessageKind] = {
    JobType.SEND_CONFIRMATION: MessageKind(
        "message.confirmation.subject", "message.confirmation.intro", True, False
    ),
    JobType.SEND_CHANGE: MessageKind("message.change.subject", "message.change.intro", True, False),
    JobType.SEND_REMINDER: MessageKind(
        "message.reminder.subject", "message.reminder.intro", True, False
    ),
    JobType.SEND_CANCELLATION: MessageKind(
        "message.cancellation.subject", "message.cancellation.intro", False, True
    ),
    JobType.SEND_SUSPENSION: MessageKind(
        "message.suspension.subject",
        "message.suspension.intro",
        True,
        False,
        "message.manage_suspended",
        closure_notice=True,
    ),
    JobType.SEND_CLOSURE_UPDATE: MessageKind(
        "message.closure_update.subject",
        "message.closure_update.intro",
        True,
        False,
        "message.manage_suspended",
        closure_notice=True,
    ),
    JobType.SEND_REINSTATEMENT: MessageKind(
        "message.reinstatement.subject", "message.reinstatement.intro", True, False
    ),
}


class MessageComposer:
    """Builds the email for a message job.

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
        closure: Closure | None = None,
    ) -> OutboundMessage:
        """Return the email for ``job_type``; closure messages include ``closure``'s notice.

        Raises:
            KeyError: If ``job_type`` is not a message job.
            ValueError: If the booking's personal data has been purged, or a closure message
                has no closure.
        """
        kind = MESSAGE_KINDS[job_type]
        if booking.contact is None:
            raise ValueError("booking has no contact details")
        if kind.closure_notice and closure is None:
            raise ValueError("closure message without a closure")
        _ = self._translator.bind(booking.locale, group.organisation_id)
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
            "notice_label": _("message.closure_notice"),
            "notice": closure.notice(locale) if closure and on_hold else None,
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
        _: BoundGettext,
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
