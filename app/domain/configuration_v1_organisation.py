"""The organisation of the version-1 configuration format (ADR 0099 points 2, 3, 7).

An organisation is addressed by its ``slug``. This slice declares its own fields (name,
identity-provider value, locale, branding, contact block, embedding origins, holiday-calendar
choice) and the collections that belong to the organisation itself: its class D settings, its
own texts, its logo and its fonts. The assembler adds the collections other slices declare
(channels, identity providers, closure scenarios, groups).

Every rule calls what the admin screens call: ``check_name_and_slug``, ``Branding`` and the
theme tokens, ``Logo`` and ``FontFile``, ``parse_origins``, ``clean_override``,
``check_term_pair``, ``contacts_check`` and ``setting_value``. The rules across objects
(``INTEGRITY``) read what a document would leave: they refuse an idp key that two
organisations would share, a deleted organisation an API client still names, a settings
combination the environment's rules forbid, a half-set agent term and a calendar that does not
exist.
"""

from collections.abc import Iterable, Mapping
from typing import Final

from app.domain.agent_term import TERM_KEYS, check_term_pair
from app.domain.authorization import Permission
from app.domain.branding import (
    DEFAULT_ACCENT,
    MAX_DISPLAY_NAME_LENGTH,
    Branding,
    FontFile,
    FontRole,
    HeaderStyle,
    Logo,
)
from app.domain.configuration_changes import (
    IntegrityRule,
    Refusal,
    Resolution,
    RuleContext,
)
from app.domain.configuration_checks import contacts_check, setting_value
from app.domain.configuration_schema import (
    Anchor,
    Collection,
    Kind,
    Shape,
    binary,
    json_object,
    setting,
    strings,
    text,
)
from app.domain.configuration_tree import Entity, path_of, walk
from app.domain.configuration_values import Binary, Value
from app.domain.embedding import MAX_ORIGIN_LENGTH, parse_origins
from app.domain.entitlements import is_idp_key
from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import RESERVED_SLUGS, check_name_and_slug, is_slug
from app.domain.settings_registry import BY_KEY, SettingClass
from app.domain.settings_service import editable
from app.domain.text_overrides import clean_override
from app.domain.theme import Theme

DEFAULT_MAX_ORIGINS: Final = 20
"""The limit of ``embedding_max_origins`` (class C) when the context does not carry it."""

DEFAULT_MAX_FONT_UNPACKED_BYTES: Final = 2_000_000
"""The default of ``max_font_unpacked_bytes`` (class C) when the context does not carry it."""

_BRANDING_FIELDS = ("display_name", "accent_color", "theme_tokens", "header_style")
_LOCALE_LENGTH = 8
_KEY_LENGTH = 64


def _invalid(field: str) -> DomainError:
    """Return the refusal that names ``field``."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, field)


# --- fields of the organisation ------------------------------------------------------------


def _check_display_name(value: Value, context: RuleContext) -> Value:
    """Return the display name as ``Branding`` stores it (trimmed, empty is none)."""
    if not isinstance(value, str):
        raise _invalid("display_name")
    return Branding(display_name=value).display_name


def _check_accent(value: Value, context: RuleContext) -> Value:
    """Return the accent colour upper-cased, if it has the contrast the pages need."""
    if not isinstance(value, str):
        raise _invalid("accent_color")
    return Branding(accent_color=value).accent_color


def _check_theme(value: Value, context: RuleContext) -> Value:
    """Return the theme tokens as the portal stores them; an unknown token is refused."""
    if not isinstance(value, dict):
        raise _invalid("theme_tokens")
    tokens: dict[str, str] = {}
    for name, token in value.items():
        if not isinstance(token, str):
            raise _invalid(name.replace("-", "_"))
        tokens[name] = token
    return dict(Theme.of(tokens).values)


def _check_origins(value: Value, context: RuleContext) -> Value:
    """Return the embedding origins as a browser sends them, in order and without repeats."""
    if not isinstance(value, list) or any(
        not isinstance(item, str) or "\n" in item or "\r" in item for item in value
    ):
        raise _invalid("origins")
    limit = context.settings.get("embedding_max_origins", DEFAULT_MAX_ORIGINS)
    parsed = parse_origins(
        "\n".join(str(item) for item in value),
        allow_local_http=not context.production,
        limit=limit if isinstance(limit, int) else DEFAULT_MAX_ORIGINS,
    )
    return list(parsed.origins)


def _check_calendars(value: Value, context: RuleContext) -> Value:
    """Return the chosen calendar slugs without repeats; each must look like a slug."""
    if not isinstance(value, list):
        raise _invalid("holiday_calendars")
    chosen: list[str] = []
    for item in value:
        if not isinstance(item, str) or not is_slug(item):
            raise _invalid("holiday_calendars")
        if item not in chosen:
            chosen.append(item)
    return list(chosen)


def _touched(merged: Mapping[str, Value], current: Mapping[str, Value] | None) -> bool:
    """Whether any branding field differs from the current object (always on create)."""
    if current is None:
        return True
    return any(merged.get(name) != current.get(name) for name in _BRANDING_FIELDS)


def _optional_text(value: Value) -> str | None:
    """Return ``value`` when it is a text, else ``None``."""
    return value if isinstance(value, str) else None


def _theme_of(tokens: Value) -> Theme:
    """Return the theme of stored or given tokens (they were checked field by field)."""
    return Theme.of({str(n): str(v) for n, v in tokens.items()} if isinstance(tokens, dict) else {})


def _check_organisation(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> dict[str, Value]:
    """Check an organisation as the admin forms do, on the fields as they will be."""
    fields: dict[str, Value] = dict(merged)
    slug, name = str(fields["slug"]), str(fields.get("name", ""))
    check_name_and_slug(name, slug)
    if current is None and slug in RESERVED_SLUGS:
        raise DomainError(ErrorCode.SLUG_TAKEN, "slug")
    if fields.get("default_locale", "nl") not in context.locales:
        raise _invalid("default_locale")
    idp_key = fields.get("idp_key")
    if idp_key is not None and not (isinstance(idp_key, str) and is_idp_key(idp_key)):
        raise _invalid("idp_key")
    if _touched(fields, current):
        branding = Branding(
            display_name=_optional_text(fields.get("display_name")),
            accent_color=str(fields.get("accent_color", DEFAULT_ACCENT)),
            theme=_theme_of(fields.get("theme_tokens")),
            header_style=HeaderStyle(str(fields.get("header_style", HeaderStyle.STANDARD.value))),
        )
        failing = branding.contrast_failure()
        if failing is not None:
            raise _invalid(failing)
    return fields


# --- the collections of the organisation ---------------------------------------------------


def _check_setting(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> dict[str, Value]:
    """Check one class D setting: a known, editable key and a value within the portal bounds."""
    fields: dict[str, Value] = dict(merged)
    key = str(fields["key"])
    spec = BY_KEY.get(key)
    if spec is None or spec.class_ is not SettingClass.ORGANISATION or not editable(spec):
        raise DomainError(ErrorCode.SETTING_NOT_EDITABLE, key if spec is not None else "key")
    fields["value"] = setting_value(spec, fields["value"], production=context.production)
    return fields


def _check_text(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> dict[str, Value]:
    """Check one text override: a locale of the installation, a key on the allow-list."""
    fields: dict[str, Value] = dict(merged)
    if fields["locale"] not in context.locales:
        raise _invalid("locale")
    # The names other roles carry (ADR 0077) are not known here; the adapter's apply checks them.
    fields["text"] = clean_override(str(fields["key"]), str(fields["text"]))
    return fields


def _check_logo(value: Value, context: RuleContext) -> Value:
    """Return the logo with the media type its bytes show; refuse what is not an image."""
    if not isinstance(value, Binary):
        raise _invalid("logo")
    logo = Logo.from_upload(value.data)
    return Binary(logo.media_type, logo.data)


def _check_font(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> dict[str, Value]:
    """Check a font by its structure and the ceiling for its unpacked size (ADR 0092)."""
    fields: dict[str, Value] = dict(merged)
    data = fields["data"]
    if not isinstance(data, Binary):
        raise _invalid("font")
    ceiling = context.settings.get("max_font_unpacked_bytes", DEFAULT_MAX_FONT_UNPACKED_BYTES)
    FontFile.from_upload(
        FontRole(str(fields["role"])),
        data.data,
        ceiling if isinstance(ceiling, int) else DEFAULT_MAX_FONT_UNPACKED_BYTES,
    )
    fields["data"] = Binary("font/woff2", data.data)
    return fields


ORGANISATION_SETTING = Kind(
    "organisation_setting",
    key=("key",),
    permission=Permission.SETTINGS_ORGANISATION_MANAGE,
    tables=frozenset({"organisation_settings"}),
    fields=(text("key", required=True, max_length=_KEY_LENGTH), setting("value")),
    check=_check_setting,
    derived=frozenset({"organisation_settings.updated_at"}),
    description="The value of one class D setting of the organisation; null removes it.",
)

ORGANISATION_TEXT = Kind(
    "organisation_text",
    key=("locale", "key"),
    permission=Permission.TEXTS_ORGANISATION_MANAGE,
    tables=frozenset({"organisation_texts"}),
    fields=(
        text("locale", required=True, max_length=_LOCALE_LENGTH),
        text("key", required=True, max_length=_KEY_LENGTH),
        text("text", required=True),
    ),
    check=_check_text,
    description="The organisation's own text for one catalogue key in one locale.",
)

ORGANISATION_LOGO = Kind(
    "organisation_logo",
    permission=Permission.BRANDING_MANAGE,
    tables=frozenset({"organisation_logos"}),
    fields=(binary("data", check=_check_logo),),
    description="The organisation's logo: PNG, JPEG or WebP.",
)

ORGANISATION_FONT = Kind(
    "organisation_font",
    key=("role",),
    permission=Permission.BRANDING_MANAGE,
    tables=frozenset({"organisation_fonts"}),
    fields=(
        text("role", required=True, choices=tuple(role.value for role in FontRole)),
        binary("data"),
    ),
    check=_check_font,
    description="An uploaded WOFF2 font of the organisation, one per role.",
)

ORGANISATION = Kind(
    "organisation",
    key=("slug",),
    permission=Permission.ORGANISATION_MANAGE,
    delete_permission=Permission.ORGANISATION_DELETE,
    anchor=Anchor.ORGANISATION,
    tables=frozenset(
        {
            "organisations",
            "organisation_contacts",
            "organisation_origins",
            "organisation_holiday_calendars",
        }
    ),
    fields=(
        text("slug", required=True, max_length=64),
        text("name", required=True, max_length=200),
        text(
            "idp_key",
            nullable=True,
            max_length=_KEY_LENGTH,
            permission=Permission.IDP_MAPPING_MANAGE,
            description="What the identity provider calls the organisation; none is the slug.",
        ),
        text("default_locale", default="nl", max_length=16),
        text(
            "display_name",
            nullable=True,
            max_length=MAX_DISPLAY_NAME_LENGTH,
            permission=Permission.BRANDING_MANAGE,
            check=_check_display_name,
        ),
        text(
            "accent_color",
            default=DEFAULT_ACCENT,
            max_length=7,
            permission=Permission.BRANDING_MANAGE,
            check=_check_accent,
        ),
        text(
            "header_style",
            default=HeaderStyle.STANDARD.value,
            choices=tuple(style.value for style in HeaderStyle),
            permission=Permission.BRANDING_MANAGE,
        ),
        json_object(
            "theme_tokens",
            default={},
            permission=Permission.BRANDING_MANAGE,
            check=_check_theme,
            description="Design tokens by name (without --booking-) to value.",
        ),
        json_object(
            "contacts",
            nullable=True,
            permission=Permission.ORGANISATION_CLOSURE,
            check=contacts_check,
            columns=("organisation_contacts.body",),
            description="The default contact block shown when participants cannot book.",
        ),
        strings(
            "origins",
            ordered=True,
            max_length=MAX_ORIGIN_LENGTH,
            permission=Permission.EMBEDDING_MANAGE,
            check=_check_origins,
            columns=("organisation_origins.origin", "organisation_origins.position"),
            description="Websites allowed to embed the groups, in order.",
        ),
        strings(
            "holiday_calendars",
            permission=Permission.HOLIDAYS_ASSIGN,
            check=_check_calendars,
            description="Slugs of the holiday calendars the organisation's groups follow.",
        ),
    ),
    collections=(
        Collection("settings", ORGANISATION_SETTING, Shape.MAP),
        Collection("texts", ORGANISATION_TEXT, Shape.MAP),
        Collection("logo", ORGANISATION_LOGO, Shape.SINGLE),
        Collection("fonts", ORGANISATION_FONT, Shape.ENTITIES),
    ),
    check=_check_organisation,
    derived=frozenset(
        {"organisations.logo_media_type", "organisations.logo_digest", "organisations.font_digests"}
    ),
    description="A client organisation, addressed by its slug.",
)
"""The organisation with the collections it owns; the assembler adds the others."""


# --- rules across objects ------------------------------------------------------------------


def _organisation_path(slug: str) -> str:
    """Return the path of an organisation in a document."""
    return path_of("", "organisations", (slug,))


def _organisations(entity: Entity) -> Iterable[tuple[str, Entity]]:
    """Yield the organisations below ``entity`` with their slugs."""
    for address, found in walk(entity, "organisation"):
        yield address[-1], found


def _idp_keys_are_unique(resolution: Resolution) -> Iterable[Refusal]:
    """Refuse an idp key that two organisations would share (the slug counts when it is none)."""
    owners: dict[str, list[str]] = {}
    for slug, found in _organisations(resolution.resulting):
        effective = found.fields.get("idp_key") or slug
        owners.setdefault(str(effective), []).append(slug)
    touched = {change.key[0] for change in resolution.changes if change.kind == "organisation"}
    for slugs in owners.values():
        if len(slugs) > 1:
            for slug in slugs:
                if slug in touched:
                    yield Refusal(_organisation_path(slug), ErrorCode.IDP_KEY_TAKEN, "idp_key")


def _deleted_organisations_have_no_clients(resolution: Resolution) -> Iterable[Refusal]:
    """Refuse to delete an organisation while the result still has an API client of it."""
    named = {
        found.fields.get("organisation") for _, found in walk(resolution.resulting, "api_client")
    }
    for change in resolution.deletions:
        if change.kind == "organisation" and change.key[0] in named:
            yield Refusal(change.path, ErrorCode.ORGANISATION_HAS_API_CLIENTS)


def _settings_do_not_combine_badly(resolution: Resolution) -> Iterable[Refusal]:
    """Refuse class D values that break a cross-field rule of the settings (ADR 0042)."""
    check = resolution.context.combination_problem
    if check is None:
        return
    changed = {c.organisation for c in resolution.changes if c.kind == "organisation_setting"}
    changed |= {c.organisation for c in resolution.deletions if c.kind == "organisation_setting"}
    for slug, found in _organisations(resolution.resulting):
        if slug not in changed:
            continue
        values = {e.key[0]: e.fields["value"] for e in found.children.get("settings", ())}
        problem = check(values)
        if problem is not None:
            where = path_of(_organisation_path(slug), "settings", ())
            yield Refusal(where, ErrorCode.SETTING_COMBINATION, problem)


def _agent_terms_come_in_pairs(resolution: Resolution) -> Iterable[Refusal]:
    """Refuse a locale where only one of the two words of the agent term would be set."""
    touched = {
        (c.organisation, c.key[0], c.path)
        for c in (*resolution.changes, *resolution.deletions)
        if c.kind == "organisation_text" and c.key[1] in TERM_KEYS
    }
    texts_of = {
        slug: {e.key: str(e.fields["text"]) for e in found.children.get("texts", ())}
        for slug, found in _organisations(resolution.resulting)
    }
    for slug, locale, path in sorted(touched, key=lambda item: (str(item[0]), item[1], item[2])):
        texts = texts_of.get(str(slug), {})
        words: dict[str, str | None] = {key: texts.get((locale, key)) for key in TERM_KEYS}
        try:
            check_term_pair(words)
        except DomainError as error:
            yield Refusal(path, error.code, error.field)


def _calendars_exist(resolution: Resolution) -> Iterable[Refusal]:
    """Refuse a chosen holiday calendar that the result does not have."""
    known = {address[-1] for address, _ in walk(resolution.resulting, "holiday_calendar")}
    resulting = dict(_organisations(resolution.resulting))
    for change in resolution.changes:
        if change.kind != "organisation" or "holiday_calendars" not in change.fields:
            continue
        chosen = resulting[change.key[0]].fields.get("holiday_calendars")
        if isinstance(chosen, list) and any(slug not in known for slug in chosen):
            yield Refusal(change.path, ErrorCode.HOLIDAY_CALENDAR_NOT_FOUND, "holiday_calendars")


INTEGRITY: tuple[IntegrityRule, ...] = (
    _idp_keys_are_unique,
    _deleted_organisations_have_no_clients,
    _settings_do_not_combine_badly,
    _agent_terms_come_in_pairs,
    _calendars_exist,
)
"""The rules across objects this slice owns."""
