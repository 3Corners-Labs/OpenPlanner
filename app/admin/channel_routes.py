"""Admin routes for an organisation's message channels: SMTP and SMS gateway (ADR 0022).

Every route depends on exactly one ``RequirePermission(integrations.manage)`` (superuser
only, HANDOVER §6); forms and changes are ``fresh``. Changes are POST forms with the Fetch
Metadata check, answered with a redirect and a confirmation. Secrets are write-only: the
password fields are always empty, a blank field keeps the stored secret (ADR 0008).
``ChannelSettingsService`` authorises again, validates and audits.

The test email goes through exactly the server the worker would use (the organisation's own,
else the installation's fallback); a failure is explained per code, never with the server's
own answer. It is rate-limited like a booking, since it sends mail to a typed-in address.

    GET  /admin/organisations/{org}/channels                   integrations.manage  forms
    POST /admin/organisations/{org}/channels/email             integrations.manage  SMTP
    POST /admin/organisations/{org}/channels/email/test        integrations.manage  test email
    POST /admin/organisations/{org}/channels/sms               integrations.manage  SMS
    POST /admin/organisations/{org}/channels/{channel}/remove  integrations.manage  remove
"""

from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin import forms
from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import Field, FormData, moment_label
from app.admin.pages import Locale, SameOrigin
from app.admin.pages import page as _page
from app.domain.authorization import Permission
from app.domain.channel_service import ChannelOverview, ChannelSettingsService
from app.domain.channel_settings import (
    ChannelConfig,
    ChannelSettings,
    SmsProvider,
    SmsSettings,
    SmtpSecurity,
    SmtpSettings,
)
from app.domain.channels import ChannelKind
from app.domain.errors import DomainError, ErrorCode
from app.i18n import BoundGettext
from app.web.errors import FormError
from app.web.locale import RequestLocale
from app.web.organisation_settings import settings_for
from app.web.protection import RejectedRequest, booking_limit, limiter

router = APIRouter(prefix="/admin", include_in_schema=False)

SECURITY_LABELS: dict[SmtpSecurity, str] = {
    SmtpSecurity.STARTTLS: "admin.channels.security_starttls",
    SmtpSecurity.TLS: "admin.channels.security_tls",
    SmtpSecurity.NONE: "admin.channels.security_none",
}

SMS_PROVIDER_LABELS: dict[SmsProvider, str] = {
    SmsProvider.WEBEX_CONNECT: "admin.channels.sms_provider_webex_connect",
}
"""Label per SMS provider (ADR 0028); a test checks that every provider has one."""

TEST_FAILURES: dict[str, str] = {
    "unreachable": "admin.channels.test_failed.unreachable",
    "tls_failed": "admin.channels.test_failed.tls_failed",
    "not_supported": "admin.channels.test_failed.not_supported",
    "unauthorized": "admin.channels.test_failed.unauthorized",
    "sender_refused": "admin.channels.test_failed.sender_refused",
    "recipient_refused": "admin.channels.test_failed.recipient_refused",
}
"""Explanation per failure code of a test email; ``rejected_<status>`` and others are generic."""

TEST_FIELD = "test_recipient"

CHANNEL_PATHS = {"email": ChannelKind.EMAIL, "sms": ChannelKind.SMS}
"""The channels in URLs; anything else is a 404."""

Manage = Annotated[
    AdminContext,
    Depends(
        RequirePermission(Permission.INTEGRATIONS_MANAGE, organisation_param="org", fresh=True)
    ),
]
"""The permission of every channel page of one organisation."""


def channel_service(request: Request) -> ChannelSettingsService:
    """Return the application's channel settings service."""
    service: ChannelSettingsService = request.app.state.channel_service
    return service


def _url(org: str) -> str:
    """Return the channels page of organisation ``org``."""
    return f"/admin/organisations/{org}/channels"


def _value(form: FormData, name: str, stored: object) -> str:
    """Return the submitted value of ``name``, else the stored one ('' for none)."""
    if name in form.values:
        return form.values[name]
    return "" if stored is None else str(stored)


def _secret_field(name: str, label: str, hint: str, form: FormData, required: bool) -> Field:
    """Return a write-only secret input: always empty, never echoed back."""
    return Field(
        name,
        label,
        "password",
        "",
        hint=hint,
        error=form.error_for(name),
        required=required,
        attributes={"autocomplete": "new-password", "spellcheck": "false"},
    )


def _smtp_fields(form: FormData, stored: ChannelSettings | None, _: BoundGettext) -> list[Field]:
    """Return the SMTP inputs, pre-filled with the stored settings."""
    config = stored.config if stored and isinstance(stored.config, SmtpSettings) else None
    security = config.security.value if config else SmtpSecurity.STARTTLS.value
    return [
        Field(
            "smtp_host",
            _("admin.channels.smtp_host"),
            value=_value(form, "smtp_host", config.host if config else None),
            hint=_("admin.channels.smtp_host_hint"),
            error=form.error_for("smtp_host"),
            attributes={"autocapitalize": "none", "spellcheck": "false"},
        ),
        Field(
            "smtp_port",
            _("admin.channels.smtp_port"),
            "number",
            _value(form, "smtp_port", config.port if config else 587),
            hint=_("admin.channels.smtp_port_hint"),
            error=form.error_for("smtp_port"),
            attributes={"min": "1", "max": "65535", "inputmode": "numeric"},
        ),
        Field(
            "smtp_security",
            _("admin.channels.smtp_security"),
            "select",
            form.values.get("smtp_security", security),
            error=form.error_for("smtp_security"),
            options=tuple(forms.Option(s.value, _(label)) for s, label in SECURITY_LABELS.items()),
        ),
        Field(
            "smtp_username",
            _("admin.channels.smtp_username"),
            value=_value(form, "smtp_username", config.username if config else None),
            hint=_("admin.channels.smtp_username_hint"),
            error=form.error_for("smtp_username"),
            required=False,
            attributes={"autocomplete": "off", "autocapitalize": "none", "spellcheck": "false"},
        ),
        _secret_field(
            "smtp_credential",
            _("admin.channels.smtp_password"),
            _("admin.channels.secret_hint_set")
            if stored and stored.secret_set
            else _("admin.channels.smtp_password_hint"),
            form,
            required=False,
        ),
        Field(
            "sender_address",
            _("admin.channels.sender_address"),
            "email",
            _value(form, "sender_address", config.sender_address if config else None),
            hint=_("admin.channels.sender_address_hint"),
            error=form.error_for("sender_address"),
            attributes={"autocomplete": "off", "spellcheck": "false"},
        ),
        Field(
            "sender_name",
            _("admin.channels.sender_name"),
            value=_value(form, "sender_name", config.sender_name if config else None),
            hint=_("admin.channels.sender_name_hint"),
            error=form.error_for("sender_name"),
            required=False,
        ),
    ]


def _sms_fields(form: FormData, stored: ChannelSettings | None, _: BoundGettext) -> list[Field]:
    """Return the SMS gateway inputs, pre-filled with the stored settings."""
    config = stored.config if stored and isinstance(stored.config, SmsSettings) else None
    provider = config.provider.value if config else SmsProvider.WEBEX_CONNECT.value
    return [
        Field(
            "sms_provider",
            _("admin.channels.sms_provider"),
            "select",
            form.values.get("sms_provider", provider),
            hint=_("admin.channels.sms_provider_hint"),
            error=form.error_for("sms_provider"),
            options=tuple(
                forms.Option(p.value, _(label)) for p, label in SMS_PROVIDER_LABELS.items()
            ),
        ),
        Field(
            "sms_api_base_url",
            _("admin.channels.sms_api_base_url"),
            "url",
            _value(form, "sms_api_base_url", config.api_base_url if config else None),
            hint=_("admin.channels.sms_api_base_url_hint"),
            error=form.error_for("sms_api_base_url"),
            attributes={"autocapitalize": "none", "spellcheck": "false"},
        ),
        _secret_field(
            "sms_api_key",
            _("admin.channels.sms_api_key"),
            _("admin.channels.secret_hint_set")
            if stored and stored.secret_set
            else _("admin.channels.sms_api_key_hint"),
            form,
            required=not (stored and stored.secret_set),
        ),
        Field(
            "sms_sender_id",
            _("admin.channels.sms_sender_id"),
            value=_value(form, "sms_sender_id", config.sender_id if config else None),
            hint=_("admin.channels.sms_sender_id_hint"),
            error=form.error_for("sms_sender_id"),
            attributes={"spellcheck": "false"},
        ),
        Field(
            "sms_country_code",
            _("admin.channels.sms_country_code"),
            value=_value(form, "sms_country_code", config.country_code if config else "31"),
            hint=_("admin.channels.sms_country_code_hint"),
            error=form.error_for("sms_country_code"),
            attributes={"inputmode": "numeric", "maxlength": "3"},
        ),
    ]


def _test_fields(form: FormData, _: BoundGettext) -> list[Field]:
    """Return the input of the test email form: where to send it."""
    return [
        Field(
            TEST_FIELD,
            _("admin.channels.test_recipient"),
            "email",
            form.text(TEST_FIELD),
            hint=_("admin.channels.test_recipient_hint"),
            error=form.error_for(TEST_FIELD),
            attributes={"autocomplete": "email", "spellcheck": "false"},
        )
    ]


def _test_failure(failure: str, _: BoundGettext) -> str:
    """Return the explanation of a failed test email."""
    if failure in TEST_FAILURES:
        return _(TEST_FAILURES[failure])
    status = failure.removeprefix("rejected_")
    if status != failure and status.isdigit():
        return _("admin.channels.test_failed.rejected", status=status)
    return _("admin.channels.test_failed.other")


def _status(stored: ChannelSettings | None, locale: RequestLocale, tz: ZoneInfo) -> dict[str, str]:
    """Return the status chip of a channel: label, tone and when it last changed."""
    _ = locale.gettext
    if stored is None:
        return {"label": _("admin.channels.not_configured"), "tone": "", "changed": ""}
    at: datetime = stored.updated_at
    changed = moment_label(at, tz, locale.code)
    if not stored.ready:
        return {"label": _("admin.channels.secret_missing"), "tone": "warning", "changed": changed}
    return {"label": _("admin.channels.ready"), "tone": "success", "changed": changed}


async def _form_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    submitted: dict[ChannelKind, FormData],
    status: int = 200,
    test: FormData | None = None,
) -> HTMLResponse:
    """Render both channel forms and the test email form with the stored settings."""
    overview: ChannelOverview = await channel_service(request).overview(context.principal, org)
    _ = locale.gettext
    tz = ZoneInfo(settings_for(request).admin_timezone)
    email = submitted.get(ChannelKind.EMAIL) or FormData({}, _)
    sms = submitted.get(ChannelKind.SMS) or FormData({}, _)
    test = test or FormData({}, _)
    stored_email = overview.channels[ChannelKind.EMAIL]
    stored_sms = overview.channels[ChannelKind.SMS]
    response = _page(
        request,
        locale,
        "admin/channels.html",
        context.principal,
        {
            "organisation": overview.organisation,
            "email_fields": _smtp_fields(email, stored_email, _),
            "sms_fields": _sms_fields(sms, stored_sms, _),
            "email_status": _status(stored_email, locale, tz),
            "sms_status": _status(stored_sms, locale, tz),
            "email_stored": stored_email is not None,
            "sms_stored": stored_sms is not None,
            "fallback_mail": bool(request.app.state.settings.smtp_host),
            "test_fields": _test_fields(test, _),
            "errors": email.errors + sms.errors + test.errors,
            "done": forms.done_message(request, _),
        },
    )
    response.status_code = status
    return response


@router.get("/organisations/{org}/channels", response_class=HTMLResponse)
async def channels(request: Request, locale: Locale, org: str, context: Manage) -> Response:
    """Show the SMTP and SMS forms of one organisation."""
    return await _form_page(request, locale, context, org, {})


async def _save(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    channel: ChannelKind,
    form: FormData,
    config: ChannelConfig | None,
    secret: str | None,
) -> Response:
    """Save ``config`` or show the form again with the refusal linked to its field."""
    if config is not None and not form.errors:
        try:
            changed = await channel_service(request).save(context.principal, org, config, secret)
        except DomainError as error:
            forms.raise_refusal(error)
            form.refusal(error)
        else:
            return forms.redirect_done(_url(org), "saved" if changed else "unchanged")
    return await _form_page(request, locale, context, org, {channel: form}, 422)


@router.post("/organisations/{org}/channels/email", response_class=HTMLResponse)
async def save_email(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Save the organisation's SMTP server; a blank password keeps the stored one."""
    form = await forms.read_form(request, locale)
    port = form.integer("smtp_port")
    security = form.choice("smtp_security", {s.value for s in SmtpSecurity})
    config: SmtpSettings | None = None
    if port is not None and security is not None:
        try:
            config = SmtpSettings(
                host=form.text("smtp_host"),
                port=port,
                security=SmtpSecurity(security),
                username=form.optional_text("smtp_username"),
                sender_address=form.text("sender_address"),
                sender_name=form.optional_text("sender_name"),
            )
        except DomainError as error:
            form.refusal(error)
    secret = form.values.get("smtp_credential") or None
    return await _save(request, locale, context, org, ChannelKind.EMAIL, form, config, secret)


@router.post("/organisations/{org}/channels/email/test", response_class=HTMLResponse)
@limiter.limit(booking_limit)
async def send_test_email(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Send a test email through the server the worker would use, and say how it went."""
    gettext = locale.gettext
    form = await forms.read_form(request, locale)
    try:
        failure = await channel_service(request).send_test_email(
            context.principal,
            org,
            form.text(TEST_FIELD).strip(),
            gettext("admin.channels.test_subject"),
            gettext("admin.channels.test_text"),
        )
    except DomainError as error:
        forms.raise_refusal(error)
        if error.code is ErrorCode.CHANNEL_NOT_CONFIGURED:
            form.errors.append(
                FormError(TEST_FIELD, gettext("admin.channels.test_failed.no_server"))
            )
        else:
            form.refusal(error)
    else:
        if failure is None:
            return forms.redirect_done(_url(org), "test_sent")
        form.errors.append(FormError(TEST_FIELD, _test_failure(failure, gettext)))
    return await _form_page(request, locale, context, org, {}, 422, form)


@router.post("/organisations/{org}/channels/sms", response_class=HTMLResponse)
async def save_sms(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Save the organisation's SMS gateway; a blank API key keeps the stored one."""
    form = await forms.read_form(request, locale)
    provider = form.choice("sms_provider", {p.value for p in SmsProvider})
    config: SmsSettings | None = None
    if provider is not None:
        try:
            config = SmsSettings(
                provider=SmsProvider(provider),
                api_base_url=form.text("sms_api_base_url"),
                sender_id=form.text("sms_sender_id"),
                country_code=form.text("sms_country_code").lstrip("+"),
            )
        except DomainError as error:
            form.refusal(error)
    secret = form.values.get("sms_api_key") or None
    return await _save(request, locale, context, org, ChannelKind.SMS, form, config, secret)


@router.post("/organisations/{org}/channels/{channel}/remove", response_class=HTMLResponse)
async def remove_channel(
    request: Request, org: str, channel: str, _: SameOrigin, context: Manage
) -> Response:
    """Remove a channel's settings and secret."""
    kind = CHANNEL_PATHS.get(channel)
    if kind is None:
        raise RejectedRequest(404)
    try:
        await channel_service(request).remove(context.principal, org, kind)
    except DomainError as error:
        forms.raise_refusal(error)
        raise
    return forms.redirect_done(_url(org), "removed")
