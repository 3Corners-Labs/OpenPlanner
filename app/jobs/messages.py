"""Compose the citizen's messages from catalogue texts (ADR 0009).

Every message uses one fixed layout (HTML plus plain text); only text blocks come from the
i18n catalogues. Placeholders are filled by the safe formatter in ``app.i18n``. The message
carries the first name, date, time, team and the management link; nothing else.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from app.domain.booking import Booking
from app.domain.channels import ChannelKind
from app.domain.job_plan import JobType, change_deadline
from app.domain.ports import Attachment, OutboundMessage
from app.domain.scheduling import Group
from app.domain.tokens import derive_token
from app.i18n import Translator
from app.i18n.dates import format_clock, format_day, local_day
from app.jobs.calendar import build_ics
from app.web.templates import create_environment

TEMPLATE_DIR = Path(__file__).parent / "templates"


@dataclass(frozen=True)
class MessageKind:
    """Catalogue keys of one message type, written out so the catalogue test can see them."""

    subject_key: str
    intro_key: str
    includes_manage_link: bool
    cancels_event: bool


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
        self, job_type: JobType, booking: Booking, group: Group, now: datetime
    ) -> OutboundMessage:
        """Return the email for ``job_type``.

        Raises:
            KeyError: If ``job_type`` is not a message job.
            ValueError: If the booking's personal data has been purged.
        """
        kind = MESSAGE_KINDS[job_type]
        if booking.contact is None:
            raise ValueError("booking has no contact details")
        _ = self._translator.bind(booking.locale, group.organisation_id)
        tz, locale = group.tz, booking.locale
        date = format_day(local_day(booking.start, tz), locale)
        start_time = format_clock(booking.start, tz, locale)
        end_time = format_clock(booking.end, tz, locale)
        deadline = change_deadline(booking.start, group.dispatch_lead)
        params = {"date": date, "time": start_time, "group_name": group.name}
        manage_url = self.manage_url(booking) if kind.includes_manage_link else None
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
            "manage_text": _(
                "message.manage",
                deadline=f"{format_day(local_day(deadline, tz), locale)}, "
                f"{format_clock(deadline, tz, locale)}",
            ),
            "manage_link": _("message.manage_link"),
            "signoff": _("message.signoff", group_name=group.name),
        }
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
        return OutboundMessage(
            channel=ChannelKind.EMAIL,
            recipient=booking.contact.email,
            subject=str(context["subject"]),
            text=self._environment.get_template("email/message.txt").render(context),
            html=self._environment.get_template("email/message.html").render(context),
            attachments=(Attachment("afspraak.ics", "text/calendar", calendar),),
        )
