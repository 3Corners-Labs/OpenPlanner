"""Admin routes for an organisation's own texts (ADR 0004, ADR 0074).

Every route depends on exactly one ``RequirePermission(texts.organisation.manage)``; changes are
``fresh``. Changes are POST forms with the Fetch Metadata check, answered with a redirect and a
confirmation. ``OrganisationTextService`` authorises again, validates and audits. The screen
belongs to *Organisation settings* (ADR 0047): it is a sub-page of the organisation's general
tab, reached from there.

    GET  /admin/organisations/{org}/texts               texts.organisation.manage  default locale
    GET  /admin/organisations/{org}/texts/{text_locale} texts.organisation.manage  editor
    POST /admin/organisations/{org}/texts/{text_locale} texts.organisation.manage  save

The editor shows the text in effect (the organisation's own, else the shipped one). Saving the
shipped text unchanged, or an empty field, keeps the default; only real changes are stored.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import Field, FormData, done_message, raise_refusal, read_form, redirect_done
from app.admin.message_routes import PART_LABELS, PURPOSE_LABELS, LocaleTab
from app.admin.pages import Locale, SameOrigin, page
from app.domain.authorization import Permission
from app.domain.channels import ChannelKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.message_texts import BLOCKS_BY_KEY
from app.domain.organisation_text_service import OrganisationTextService, OrganisationTextView
from app.domain.text_overrides import EDITABLE_BY_KEY, EDITABLE_TEXTS, Area, EditableText
from app.i18n import BoundGettext, Translator
from app.web.errors import FormError
from app.web.locale import RequestLocale
from app.web.protection import RejectedRequest

router = APIRouter(prefix="/admin", include_in_schema=False)

Open = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.TEXTS_ORGANISATION_MANAGE, organisation_param="org")),
]
Change = Annotated[
    AdminContext,
    Depends(
        RequirePermission(
            Permission.TEXTS_ORGANISATION_MANAGE, organisation_param="org", fresh=True
        )
    ),
]

PAGE_LABELS: dict[str, str] = {
    "group.intro": "admin.org_texts.page.group_intro",
    "direct.card.title": "admin.org_texts.page.direct_card_title",
    "direct.card.text": "admin.org_texts.page.direct_card_text",
    "direct.intro": "admin.org_texts.page.direct_intro",
    "direct.unavailable": "admin.org_texts.page.direct_unavailable",
    "booked.title": "admin.org_texts.page.booked_title",
    "booked.text": "admin.org_texts.page.booked_text",
    "booked.lapse": "admin.org_texts.page.booked_lapse",
    "booked.no_mail": "admin.org_texts.page.booked_no_mail",
    "confirm.text": "admin.org_texts.page.confirm_text",
    "confirm.not_me": "admin.org_texts.page.confirm_not_me",
    "manage.pending.text": "admin.org_texts.page.manage_pending_text",
}
"""The field label of each participant-page text."""


@dataclass(frozen=True)
class TextSection:
    """The texts of one message or of the pages, as one card of the editor."""

    title: str
    fields: list[Field]


def _service(request: Request) -> OrganisationTextService:
    """Return the application's organisation text service."""
    service: OrganisationTextService = request.app.state.organisation_text_service
    return service


def _translator(request: Request) -> Translator:
    """Return the application's translator."""
    translator: Translator = request.app.state.translator
    return translator


def _url(org: str, text_locale: str) -> str:
    """Return the editor of organisation ``org`` in ``text_locale``."""
    return f"/admin/organisations/{org}/texts/{text_locale}"


def _label(spec: EditableText, _: BoundGettext) -> str:
    """Return the field label of an editable text."""
    if spec.area is Area.PAGES:
        return _(PAGE_LABELS[spec.key])
    block = BLOCKS_BY_KEY[spec.key]
    return _(
        "admin.messages.part_sms" if block.channel is ChannelKind.SMS else PART_LABELS[block.part]
    )


def _section_title(spec: EditableText, _: BoundGettext) -> tuple[str, str]:
    """Return the id and title of the card an editable text belongs to."""
    if spec.area is Area.PAGES:
        return "pages", _("admin.org_texts.section_pages")
    purpose = BLOCKS_BY_KEY[spec.key].purpose
    return purpose, _(PURPOSE_LABELS[purpose])


def _hint(spec: EditableText, custom: bool, _: BoundGettext) -> str:
    """Return whether the organisation changed the text and which placeholders it may use."""
    state = _("admin.org_texts.custom") if custom else _("admin.messages.default")
    if not spec.placeholders:
        return state
    names = ", ".join(f"{{{name}}}" for name in sorted(spec.placeholders))
    return f"{state} {_('admin.messages.placeholders', names=names)}"


def _sections(
    request: Request,
    text_locale: str,
    stored: dict[tuple[str, str], str],
    form: FormData,
    _: BoundGettext,
) -> list[TextSection]:
    """Return one section per message and one for the pages, with the text in effect."""
    translator = _translator(request)
    sections: dict[str, TextSection] = {}
    for spec in EDITABLE_TEXTS:
        own = stored.get((text_locale, spec.key))
        default = translator.lookup(text_locale, spec.key)
        field = Field(
            spec.key,
            _label(spec, _),
            "text" if spec.single_line else "textarea",
            form.values.get(spec.key, own if own is not None else default),
            hint=_hint(spec, own is not None, _),
            error=form.error_for(spec.key),
            required=False,
            attributes={"maxlength": str(spec.max_length), "spellcheck": "true"}
            | ({} if spec.single_line else {"rows": "4"}),
        )
        section_id, title = _section_title(spec, _)
        sections.setdefault(section_id, TextSection(title, [])).fields.append(field)
    return list(sections.values())


async def _editor(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    text_locale: str,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the editor of one locale."""
    view: OrganisationTextView = await _service(request).view(context.principal, org)
    translator = _translator(request)
    tabs = [
        LocaleTab(code, translator.gettext(code, "language.autonym"), code == text_locale)
        for code in translator.locales
    ]
    response = page(
        request,
        locale,
        "admin/organisation_texts.html",
        context.principal,
        {
            "organisation": view.organisation,
            "text_locale": text_locale,
            "locale_tabs": tabs,
            "sections": _sections(request, text_locale, view.texts, form, locale.gettext),
            "errors": form.errors,
            "done": done_message(request, locale.gettext),
        },
    )
    response.status_code = status
    return response


def _text_locale(request: Request, value: str) -> str:
    """Return ``value`` if texts can be written in it, else refuse with 404."""
    if value not in _translator(request).locales:
        raise RejectedRequest(404)
    return value


@router.get("/organisations/{org}/texts", response_class=HTMLResponse)
async def texts(request: Request, org: str, context: Open) -> Response:
    """Open the editor in the default locale."""
    return RedirectResponse(_url(org, _translator(request).locales[0]), status_code=303)


@router.get("/organisations/{org}/texts/{text_locale}", response_class=HTMLResponse)
async def texts_in(
    request: Request, locale: Locale, org: str, text_locale: str, context: Open
) -> HTMLResponse:
    """Show the organisation's texts of one locale."""
    code = _text_locale(request, text_locale)
    return await _editor(request, locale, context, org, code, FormData({}, locale.gettext))


@router.post("/organisations/{org}/texts/{text_locale}", response_class=HTMLResponse)
async def save_texts(
    request: Request, locale: Locale, org: str, text_locale: str, _: SameOrigin, context: Change
) -> Response:
    """Save the texts of one locale; an empty or unchanged shipped text keeps the default."""
    code = _text_locale(request, text_locale)
    form = await read_form(request, locale)
    translator = _translator(request)
    submitted: dict[str, str | None] = {}
    for key in EDITABLE_BY_KEY:
        if key not in form.values:
            continue
        value = form.values[key].replace("\r\n", "\n")
        shipped = translator.lookup(code, key)
        submitted[key] = None if not value.strip() or value.strip() == shipped.strip() else value
    try:
        await _service(request).save(context.principal, org, code, submitted)
    except DomainError as error:
        raise_refusal(error)
        spec = EDITABLE_BY_KEY.get(error.field or "")
        if error.code is not ErrorCode.INVALID_CONFIGURATION or spec is None:
            raise
        names = ", ".join(f"{{{name}}}" for name in sorted(spec.placeholders)) or "–"
        message = locale.gettext("admin.form.error.message_text", max=spec.max_length, names=names)
        form.errors.append(FormError(spec.key, message))
        return await _editor(request, locale, context, org, code, form, 422)
    return redirect_done(_url(org, code), "texts_saved")
