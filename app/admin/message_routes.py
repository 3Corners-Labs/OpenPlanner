"""Admin routes for a group's reminders and message texts (ADR 0022).

Every route depends on exactly one ``RequirePermission``; forms and changes are ``fresh``.
Changes are POST forms with the Fetch Metadata check, answered with a redirect and a
confirmation. ``MessagingService`` authorises again, validates and audits.

    GET  /admin/groups/{slug}/reminders                             reminders.manage  rules
    POST /admin/groups/{slug}/reminders                             reminders.manage  add
    POST /admin/groups/{slug}/reminders/{channel}/{minutes}/delete  reminders.manage  remove
    GET  /admin/groups/{slug}/messages                              texts.manage      default locale
    GET  /admin/groups/{slug}/messages/{locale}                     texts.manage      editor
    POST /admin/groups/{slug}/messages/{locale}                     texts.manage      save

The text editor shows the text in effect (the group's own, else the default). Saving the
default unchanged, or an empty field, keeps the default; only real changes are stored.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import Field, FormData, Option, raise_refusal, read_form, redirect_done
from app.admin.group_routes import group_page
from app.admin.pages import Locale, SameOrigin
from app.domain.authorization import Permission
from app.domain.channels import ChannelKind, ReminderRule
from app.domain.errors import DomainError, ErrorCode
from app.domain.message_texts import BLOCKS_BY_KEY, MESSAGE_BLOCKS, TextBlock
from app.domain.messaging_service import MessagingService, Reminders
from app.i18n import BoundGettext, Translator
from app.web.errors import FormError
from app.web.locale import RequestLocale
from app.web.protection import RejectedRequest

router = APIRouter(prefix="/admin", include_in_schema=False)

CHANNEL_LABELS: dict[ChannelKind, str] = {
    ChannelKind.EMAIL: "admin.reminders.channel_email",
    ChannelKind.SMS: "admin.reminders.channel_sms",
}
"""The channels an admin can choose for a reminder (WhatsApp waits, ADR 0009)."""

UNITS: dict[str, int] = {"minutes": 1, "hours": 60, "days": 24 * 60}
UNIT_LABELS: dict[str, str] = {
    "minutes": "admin.reminders.unit_minutes",
    "hours": "admin.reminders.unit_hours",
    "days": "admin.reminders.unit_days",
}

OFFSET_LABELS: dict[str, tuple[str, str]] = {
    "days": ("admin.reminders.offset_day", "admin.reminders.offset_days"),
    "hours": ("admin.reminders.offset_hour", "admin.reminders.offset_hours"),
    "minutes": ("admin.reminders.offset_minute", "admin.reminders.offset_minutes"),
}
"""Singular and plural label of an offset, per unit."""

PURPOSE_LABELS: dict[str, str] = {
    "confirmation": "admin.messages.purpose_confirmation",
    "change": "admin.messages.purpose_change",
    "reminder": "admin.messages.purpose_reminder",
    "cancellation": "admin.messages.purpose_cancellation",
    "all": "admin.messages.purpose_all",
}

PART_LABELS: dict[str, str] = {
    "subject": "admin.messages.part_subject",
    "intro": "admin.messages.part_intro",
    "signoff": "admin.messages.part_signoff",
    "body": "admin.messages.part_sms",
}

ManageReminders = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.REMINDERS_MANAGE, group_param="slug", fresh=True)),
]
ManageTexts = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.TEXTS_MANAGE, group_param="slug", fresh=True)),
]


def messaging_service(request: Request) -> MessagingService:
    """Return the application's messaging service."""
    service: MessagingService = request.app.state.messaging_service
    return service


def _translator(request: Request) -> Translator:
    """Return the application's translator."""
    translator: Translator = request.app.state.translator
    return translator


# --- reminders ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReminderView:
    """One rule as the reminders page shows it."""

    channel: str
    channel_label: str
    minutes_before: int
    offset: str
    ready: bool


def offset_label(minutes: int, _: BoundGettext) -> str:
    """Return ``24 uur vooraf``-style text in the largest whole unit."""
    unit = next(u for u in ("days", "hours", "minutes") if minutes % UNITS[u] == 0)
    count = minutes // UNITS[unit]
    singular, plural = OFFSET_LABELS[unit]
    return _(singular if count == 1 else plural, count=count)


def _reminder_fields(form: FormData, _: BoundGettext) -> list[Field]:
    """Return the inputs of the add-reminder form."""
    return [
        Field(
            "channel",
            _("admin.reminders.channel"),
            "select",
            form.text("channel") or ChannelKind.EMAIL.value,
            error=form.error_for("channel"),
            options=tuple(Option(c.value, _(label)) for c, label in CHANNEL_LABELS.items()),
        ),
        Field(
            "amount",
            _("admin.reminders.amount"),
            "number",
            form.text("amount") or "24",
            hint=_("admin.reminders.amount_hint"),
            error=form.error_for("amount"),
            attributes={"min": "1", "max": "43200", "inputmode": "numeric"},
        ),
        Field(
            "unit",
            _("admin.reminders.unit"),
            "select",
            form.text("unit") or "hours",
            error=form.error_for("unit"),
            options=tuple(Option(u, _(label)) for u, label in UNIT_LABELS.items()),
        ),
    ]


def _views(reminders: Reminders, fallback_mail: bool, _: BoundGettext) -> list[ReminderView]:
    """Return the rules for display; a rule is ready when its channel can deliver."""
    ready = set(reminders.ready) | ({ChannelKind.EMAIL} if fallback_mail else set())
    return [
        ReminderView(
            r.channel.value,
            _(CHANNEL_LABELS.get(r.channel, "admin.reminders.channel_other")),
            r.minutes_before,
            offset_label(r.minutes_before, _),
            r.channel in ready,
        )
        for r in reminders.rules
    ]


async def _reminders_response(
    request: Request, locale: RequestLocale, context: AdminContext, form: FormData, status: int
) -> HTMLResponse:
    """Render the rules with the add-reminder form."""
    slug = context.group.group.slug
    reminders = await messaging_service(request).reminders(context.principal, slug)
    views = _views(reminders, bool(request.app.state.settings.smtp_host), locale.gettext)
    return group_page(
        request,
        locale,
        context,
        "admin/group_reminders.html",
        {
            "rules": views,
            "not_ready": any(not v.ready for v in views),
            "asks_phone": reminders.asks_phone,
            "fields": _reminder_fields(form, locale.gettext),
            "errors": form.errors,
        },
        status,
    )


@router.get("/groups/{slug}/reminders", response_class=HTMLResponse)
async def reminders(
    request: Request, locale: Locale, slug: str, context: ManageReminders
) -> HTMLResponse:
    """Show the group's reminder rules."""
    return await _reminders_response(request, locale, context, FormData({}, locale.gettext), 200)


@router.post("/groups/{slug}/reminders", response_class=HTMLResponse)
async def add_reminder(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: ManageReminders
) -> Response:
    """Add a reminder rule."""
    form = await read_form(request, locale)
    channel = form.choice("channel", {c.value for c in CHANNEL_LABELS})
    amount = form.integer("amount")
    unit = form.choice("unit", set(UNITS))
    if not form.errors and channel and amount is not None and unit:
        try:
            rule = ReminderRule(ChannelKind(channel), amount * UNITS[unit])
            await messaging_service(request).add_reminder(context.principal, slug, rule)
        except DomainError as error:
            raise_refusal(error)
            form.refusal(error)
        else:
            return redirect_done(f"/admin/groups/{slug}/reminders", "added")
    return await _reminders_response(request, locale, context, form, 422)


@router.post("/groups/{slug}/reminders/{channel}/{minutes}/delete", response_class=HTMLResponse)
async def remove_reminder(
    request: Request,
    slug: str,
    channel: str,
    minutes: int,
    _: SameOrigin,
    context: ManageReminders,
) -> Response:
    """Remove a reminder rule."""
    try:
        kind = ChannelKind(channel)
    except ValueError as exc:
        raise RejectedRequest(404) from exc
    try:
        await messaging_service(request).remove_reminder(context.principal, slug, kind, minutes)
    except DomainError as error:
        raise_refusal(error)
        raise
    return redirect_done(f"/admin/groups/{slug}/reminders", "removed")


# --- message texts --------------------------------------------------------------------------


@dataclass(frozen=True)
class TextSection:
    """The blocks of one message, as one card of the editor."""

    title: str
    fields: list[Field]


@dataclass(frozen=True)
class LocaleTab:
    """A locale whose texts can be edited."""

    code: str
    label: str
    current: bool


def _placeholder_hint(block: TextBlock, custom: bool, _: BoundGettext) -> str:
    """Return which placeholders the block may use and whether the group changed it."""
    names = ", ".join(f"{{{name}}}" for name in sorted(block.placeholders))
    state = _("admin.messages.custom") if custom else _("admin.messages.default")
    return f"{state} {_('admin.messages.placeholders', names=names)}"


def _sections(
    request: Request,
    context: AdminContext,
    text_locale: str,
    stored: dict[tuple[str, str], str],
    form: FormData,
    _: BoundGettext,
) -> list[TextSection]:
    """Return one section per message with the text in effect per block."""
    translator = _translator(request)
    organisation_id = context.group.group.organisation_id
    sections: dict[str, TextSection] = {}
    for block in MESSAGE_BLOCKS:
        own = stored.get((text_locale, block.key))
        default = translator.lookup(text_locale, block.key, organisation_id)
        label = _(PART_LABELS[block.part])
        if block.channel is ChannelKind.SMS:
            label = _("admin.messages.part_sms")
        field = Field(
            block.key,
            label,
            "text" if block.single_line else "textarea",
            form.values.get(block.key, own if own is not None else default),
            hint=_placeholder_hint(block, own is not None, _),
            error=form.error_for(block.key),
            required=False,
            attributes={"maxlength": str(block.max_length), "spellcheck": "true"}
            | ({} if block.single_line else {"rows": "4"}),
        )
        title = _(PURPOSE_LABELS[block.purpose])
        sections.setdefault(block.purpose, TextSection(title, [])).fields.append(field)
    return list(sections.values())


async def _texts_response(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    text_locale: str,
    form: FormData,
    status: int,
) -> HTMLResponse:
    """Render the text editor of one locale."""
    stored = await messaging_service(request).texts(context.principal, context.group.group.slug)
    _ = locale.gettext
    translator = _translator(request)
    tabs = [
        LocaleTab(code, translator.gettext(code, "language.autonym"), code == text_locale)
        for code in translator.locales
    ]
    return group_page(
        request,
        locale,
        context,
        "admin/group_messages.html",
        {
            "text_locale": text_locale,
            "locale_tabs": tabs,
            "sections": _sections(request, context, text_locale, stored, form, _),
            "errors": form.errors,
        },
        status,
    )


def _text_locale(request: Request, value: str) -> str:
    """Return ``value`` if texts can be edited in it, else refuse with 404."""
    if value not in _translator(request).locales:
        raise RejectedRequest(404)
    return value


@router.get("/groups/{slug}/messages", response_class=HTMLResponse)
async def messages(request: Request, slug: str, context: ManageTexts) -> Response:
    """Open the editor in the default locale."""
    default = _translator(request).locales[0]
    return RedirectResponse(f"/admin/groups/{slug}/messages/{default}", status_code=303)


@router.get("/groups/{slug}/messages/{text_locale}", response_class=HTMLResponse)
async def messages_in(
    request: Request, locale: Locale, slug: str, text_locale: str, context: ManageTexts
) -> HTMLResponse:
    """Show the message texts of one locale."""
    code = _text_locale(request, text_locale)
    return await _texts_response(request, locale, context, code, FormData({}, locale.gettext), 200)


@router.post("/groups/{slug}/messages/{text_locale}", response_class=HTMLResponse)
async def save_messages(
    request: Request,
    locale: Locale,
    slug: str,
    text_locale: str,
    _: SameOrigin,
    context: ManageTexts,
) -> Response:
    """Save the texts of one locale; an empty or unchanged default text keeps the default."""
    code = _text_locale(request, text_locale)
    form = await read_form(request, locale)
    translator = _translator(request)
    organisation_id = context.group.group.organisation_id
    texts: dict[str, str | None] = {}
    for key in BLOCKS_BY_KEY:
        if key not in form.values:
            continue
        submitted = form.values[key].replace("\r\n", "\n")
        default = translator.lookup(code, key, organisation_id)
        texts[key] = None if not submitted or submitted == default.strip() else submitted
    try:
        await messaging_service(request).save_texts(context.principal, slug, code, texts)
    except DomainError as error:
        raise_refusal(error)
        if error.code is not ErrorCode.INVALID_CONFIGURATION or error.field not in BLOCKS_BY_KEY:
            raise
        block = BLOCKS_BY_KEY[error.field]
        names = ", ".join(f"{{{name}}}" for name in sorted(block.placeholders))
        message = locale.gettext("admin.form.error.message_text", max=block.max_length, names=names)
        form.errors.append(FormError(block.key, message))
        return await _texts_response(request, locale, context, code, form, 422)
    return redirect_done(f"/admin/groups/{slug}/messages/{code}", "texts_saved")
