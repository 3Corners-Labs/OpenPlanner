"""The platform aggregate of the version-1 configuration document (ADR 0099 point 3).

The ``platform`` section holds what the superuser configures for the whole installation:
the class C settings, the sealed platform secrets, the machine API switch, the log
destinations, the custom roles and the holiday calendars with their days. Each kind's rules
call the functions the admin screens call (settings parsing and bounds, ``validate_draft``,
``RoleDraft``, the holiday dataclasses), so a document can do nothing the portal forbids. The
rules across objects (environment locks, the combination of settings, the login suffix, a
calendar still in use) are in ``INTEGRITY``.
"""

from collections.abc import Iterator, Mapping
from datetime import date

from app.domain.authorization import Permission
from app.domain.configuration_changes import (
    Change,
    IntegrityRule,
    Refusal,
    Resolution,
    RuleContext,
)
from app.domain.configuration_checks import setting_value
from app.domain.configuration_schema import (
    Collection,
    Kind,
    Shape,
    boolean,
    secret,
    setting,
    strings,
    text,
)
from app.domain.configuration_tree import Entity, walk
from app.domain.configuration_values import Json, Secret, Value
from app.domain.errors import DomainError, ErrorCode
from app.domain.log_destinations import (
    DEFAULT_CATEGORIES,
    LEVELS,
    SHIPPED_CATEGORIES,
    DestinationDraft,
    DestinationKind,
    categories_text,
    validate_draft,
    validate_secret,
)
from app.domain.log_events import LogCategory
from app.domain.login_domains import overlaps
from app.domain.roles import RoleDraft
from app.domain.scheduling import check_name, check_name_and_slug
from app.domain.settings_registry import BY_KEY, REGISTRY, SettingClass, SettingSpec
from app.domain.settings_service import LOGIN_SUFFIX, editable

PLATFORM_MANAGE = Permission.SETTINGS_PLATFORM_MANAGE
WEBEX_SERVICE_APP = "webex_service_app"
"""The sealed tokens of the Webex Service App, written by the Webex adapter (ADR 0007)."""

TEMPORARY_SETTINGS = frozenset({"log_level"})
"""Settings the portal only sets for a limited time (ADR 0042 §6); a document never could."""

SECRET_NAMES = frozenset(
    spec.key
    for spec in REGISTRY
    if spec.class_ is SettingClass.PLATFORM and spec.secret and editable(spec)
) | {WEBEX_SERVICE_APP}
"""The names a ``secrets`` entry may have: the class C secrets the portal writes (derived
from the registry) and the Webex Service App's tokens."""


def _text(values: Mapping[str, Value], name: str) -> str:
    """Return a text field of ``values``, or the empty text when it is not one."""
    value = values.get(name)
    return value if isinstance(value, str) else ""


def _flag(values: Mapping[str, Value], name: str) -> bool:
    """Return a boolean field of ``values``; anything but ``true`` is false."""
    return values.get(name) is True


def _words(values: Mapping[str, Value], name: str) -> list[str]:
    """Return a list-of-texts field of ``values``."""
    value = values.get(name)
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def editable_setting(key: str) -> SettingSpec | None:
    """Return the registry entry of a class C setting a document may change, else ``None``.

    The portal's own rule (``settings_service.editable``) with two exclusions: secrets travel
    in ``secrets``, and a setting that only holds for a limited time (``log_level``) would
    become permanent.
    """
    spec = BY_KEY.get(key)
    if spec is None or spec.class_ is not SettingClass.PLATFORM or spec.secret:
        return None
    return spec if editable(spec) and key not in TEMPORARY_SETTINGS else None


def _check_setting(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check a platform setting: an editable class C key, a value of its type and bounds."""
    key = _text(merged, "key")
    spec = editable_setting(key)
    if spec is None:
        raise DomainError(ErrorCode.SETTING_NOT_EDITABLE, key)
    return {**merged, "value": setting_value(spec, merged["value"], production=context.production)}


def _check_secret(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check a platform secret: a name the portal writes, and a value that is not blank."""
    name = _text(merged, "name")
    if name not in SECRET_NAMES:
        raise DomainError(ErrorCode.SETTING_NOT_EDITABLE, name)
    value = merged.get("secret")
    if isinstance(value, Secret):
        plain = value.reveal().strip()
        if not plain:
            raise DomainError(ErrorCode.SETTING_INVALID, name)
        return {**merged, "secret": Secret(plain)}
    return merged


def _categories(text_value: str) -> frozenset[LogCategory]:
    """Read the stored comma-separated categories strictly: an unknown name is refused."""
    known = {str(category): category for category in SHIPPED_CATEGORIES}
    names = [part for part in text_value.split(",") if part]
    if any(name not in known for name in names):
        raise DomainError(ErrorCode.LOG_DESTINATION_INVALID, "categories")
    return frozenset(known[name] for name in names)


def _check_log_destination(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check a log destination as the logging screen does, and return it cleaned."""
    try:
        kind = DestinationKind(_text(merged, "kind"))
    except ValueError:
        raise DomainError(ErrorCode.LOG_DESTINATION_INVALID, "kind") from None
    name = _text(merged, "name")
    clean = validate_draft(
        DestinationDraft(
            name=name,
            kind=kind,
            address=_text(merged, "address"),
            index=_text(merged, "index_name"),
            source=_text(merged, "source"),
            min_level=_text(merged, "min_level"),
            categories=_categories(_text(merged, "categories")),
            include_diagnostic=_flag(merged, "include_diagnostic"),
            include_trace=_flag(merged, "include_trace"),
            ca_pem=_text(merged, "ca_pem"),
        ),
        production=context.production,
    )
    if clean.name != name:
        raise DomainError(ErrorCode.LOG_DESTINATION_INVALID, "name")
    credential = merged.get("secret")
    if isinstance(credential, Secret):
        credential = Secret(validate_secret(kind, credential.reveal()))
    elif kind.needs_credential and (current is None or current.get("secret") is None):
        raise DomainError(ErrorCode.LOG_DESTINATION_INVALID, "secret")
    return {
        **merged,
        "kind": clean.kind.value,
        "address": clean.address,
        "index_name": clean.index,
        "source": clean.source,
        "min_level": clean.min_level,
        "categories": categories_text(clean.categories),
        "ca_pem": clean.ca_pem,
        **({"secret": credential} if credential is not None else {}),
    }


def _check_role(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check a custom role: the definition's bounds, a fixed template, a free claim value."""
    claim = _text(merged, "claim_value")
    if current is not None and current.get("template") != merged.get("template"):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "template")
    draft = RoleDraft.create(
        template=_text(merged, "template"),
        permissions=_words(merged, "permissions"),
        claim_value=claim,
        name_nl=_text(merged, "name_nl"),
        name_en=_text(merged, "name_en"),
    )
    if claim.casefold() in {value.casefold() for value in context.built_in_claim_values}:
        raise DomainError(ErrorCode.CLAIM_VALUE_TAKEN, "claim_value")
    permissions: list[Json] = [*sorted(p.value for p in draft.permissions)]
    return {
        **merged,
        "name_nl": draft.name_nl,
        "name_en": draft.name_en,
        "permissions": permissions,
    }


def _check_calendar(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check a holiday calendar: a display name and a slug as organisations have them."""
    check_name_and_slug(_text(merged, "name"), _text(merged, "slug"))
    return merged


def _check_holiday(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check a holiday: an ISO date written in full, and a name."""
    day = _text(merged, "day")
    try:
        valid = date.fromisoformat(day).isoformat() == day
    except ValueError:
        valid = False
    if not valid:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "day")
    check_name(_text(merged, "name"))
    return merged


PLATFORM_SETTING = Kind(
    "platform_setting",
    key=("key",),
    permission=PLATFORM_MANAGE,
    tables=frozenset({"platform_settings"}),
    fields=(text("key", required=True, max_length=64), setting("value")),
    check=_check_setting,
    derived=frozenset({"platform_settings.updated_at", "platform_settings.expires_at"}),
    description="The portal value of one class C setting.",
)
PLATFORM_SECRET = Kind(
    "platform_secret",
    key=("name",),
    permission=PLATFORM_MANAGE,
    tables=frozenset({"integration_credentials"}),
    fields=(
        text("name", required=True, max_length=64),
        secret("secret", columns=("nonce", "ciphertext")),
    ),
    check=_check_secret,
    derived=frozenset({"integration_credentials.updated_at"}),
    description="A sealed platform secret: a class C secret setting or the Webex Service App.",
)
API_SETTINGS = Kind(
    "api_settings",
    permission=Permission.API_CLIENTS_MANAGE,
    tables=frozenset({"api_settings"}),
    fields=(boolean("enabled", default=False), boolean("docs_public", default=False)),
    derived=frozenset({"api_settings.updated_at"}),
    description="The superuser's switches for the machine API.",
)
LOG_DESTINATION = Kind(
    "log_destination",
    key=("name",),
    permission=Permission.LOGGING_MANAGE,
    tables=frozenset({"log_destinations"}),
    fields=(
        text("name", required=True, max_length=80),
        text("kind", required=True, choices=tuple(kind.value for kind in DestinationKind)),
        text("address", required=True, max_length=500),
        text("index_name", default="", max_length=120),
        text("source", default="", max_length=120),
        text("min_level", default="info", choices=LEVELS),
        text("categories", default=categories_text(DEFAULT_CATEGORIES), max_length=64),
        boolean("include_diagnostic", default=False),
        boolean("include_trace", default=False),
        boolean("enabled", default=False),
        text("ca_pem", default=""),
        secret("secret", columns=("secret_nonce", "secret_ciphertext")),
    ),
    check=_check_log_destination,
    derived=frozenset(
        {"log_destinations.created_at", "log_destinations.updated_at", "log_destinations.tested_at"}
    ),
    description="A place where copies of the log lines go.",
)
ROLE = Kind(
    "role",
    key=("claim_value",),
    permission=Permission.ROLES_MANAGE,
    tables=frozenset({"roles"}),
    fields=(
        text("claim_value", required=True, max_length=128),
        text("template", required=True, max_length=32),
        text("name_nl", required=True, max_length=80),
        text("name_en", required=True, max_length=80),
        strings("permissions"),
    ),
    check=_check_role,
    derived=frozenset({"roles.version"}),
    description="A custom role: a copy of a built-in template within its bounds.",
)
HOLIDAY = Kind(
    "holiday",
    key=("day",),
    permission=Permission.HOLIDAYS_MANAGE,
    tables=frozenset({"holidays"}),
    fields=(text("day", required=True, max_length=10), text("name", required=True, max_length=200)),
    check=_check_holiday,
    description="One closed date of a holiday calendar.",
)
HOLIDAY_CALENDAR = Kind(
    "holiday_calendar",
    key=("slug",),
    permission=Permission.HOLIDAYS_MANAGE,
    tables=frozenset({"holiday_calendars"}),
    fields=(
        text("slug", required=True, max_length=64),
        text("name", required=True, max_length=200),
    ),
    collections=(Collection("days", HOLIDAY, Shape.MAP),),
    check=_check_calendar,
    description="A reusable set of closed days.",
)
PLATFORM = Kind(
    "platform",
    permission=PLATFORM_MANAGE,
    tables=frozenset({"settings_version"}),
    deletable=False,
    collections=(
        Collection("settings", PLATFORM_SETTING, Shape.MAP),
        Collection("secrets", PLATFORM_SECRET, Shape.MAP),
        Collection("api", API_SETTINGS, Shape.SINGLE),
        Collection("log_destinations", LOG_DESTINATION, Shape.ENTITIES),
        Collection("roles", ROLE, Shape.ENTITIES),
        Collection("holiday_calendars", HOLIDAY_CALENDAR, Shape.ENTITIES),
    ),
    derived=frozenset({"settings_version.version"}),
    description="What the superuser configures for the whole installation.",
)


def _touched(resolution: Resolution, *kinds: str) -> list[Change]:
    """Return the changes (writes and deletions) of objects of the given kinds."""
    return [c for c in (*resolution.changes, *resolution.deletions) if c.kind in kinds]


def _setting_values(resolution: Resolution) -> dict[str, object]:
    """Return the portal values of the class C settings as the resulting tree holds them."""
    return {
        entity.key[0]: entity.fields["value"]
        for _, entity in walk(resolution.resulting, "platform_setting")
        if "value" in entity.fields
    }


def _locked(resolution: Resolution) -> Iterator[Refusal]:
    """Refuse a change to a setting or secret the environment locks (ADR 0042)."""
    for change in _touched(resolution, "platform_setting", "platform_secret"):
        if change.key[0] in resolution.usage.locked_settings:
            yield Refusal(change.path, ErrorCode.SETTING_LOCKED, change.key[0])


def _combination(resolution: Resolution) -> Iterator[Refusal]:
    """Refuse settings that break a rule together (ADR 0042), as the portal's change does."""
    problem = resolution.context.combination_problem
    changes = _touched(resolution, "platform_setting")
    if problem is not None and changes and problem(_setting_values(resolution)):
        yield Refusal("platform.settings", ErrorCode.SETTING_COMBINATION, "settings")


def _login_suffix(resolution: Resolution) -> Iterator[Refusal]:
    """Refuse a login suffix that overlaps a claimed login domain, and a claim that overlaps it."""
    values = _setting_values(resolution)
    suffix = values.get(
        LOGIN_SUFFIX, resolution.context.settings.get(LOGIN_SUFFIX, BY_KEY[LOGIN_SUFFIX].default)
    )
    if not isinstance(suffix, str):
        return
    for change in _touched(resolution, "platform_setting"):
        if change.key == (LOGIN_SUFFIX,) and any(
            overlaps(domain.key[0], suffix) for _, domain in _claims(resolution.resulting)
        ):
            yield Refusal(change.path, ErrorCode.IDP_DOMAIN_TAKEN, LOGIN_SUFFIX)
    for change in _touched(resolution, "identity_provider_domain"):
        if change.action.value == "create" and overlaps(change.key[0], suffix):
            yield Refusal(change.path, ErrorCode.IDP_DOMAIN_TAKEN, "domain")


def _claims(root: Entity) -> Iterator[tuple[tuple[str, ...], Entity]]:
    """Yield the claimed login domains of a tree."""
    return walk(root, "identity_provider_domain")


def _calendar_in_use(resolution: Resolution) -> Iterator[Refusal]:
    """Refuse deleting a holiday calendar an organisation or group still names."""
    deleted = {c.key[0]: c.path for c in resolution.deletions if c.kind == "holiday_calendar"}
    if not deleted:
        return
    for _, entity in walk(resolution.resulting):
        if entity.kind not in ("organisation", "group"):
            continue
        for slug in _words(entity.fields, "holiday_calendars"):
            if slug in deleted:
                yield Refusal(deleted.pop(slug), ErrorCode.HOLIDAY_CALENDAR_IN_USE)


def _integrity(resolution: Resolution) -> Iterator[Refusal]:
    """Run every rule across the platform's objects."""
    yield from _locked(resolution)
    yield from _combination(resolution)
    yield from _login_suffix(resolution)
    yield from _calendar_in_use(resolution)


INTEGRITY: tuple[IntegrityRule, ...] = (_integrity,)
"""The rules across objects of this slice: environment locks, the combination of settings,
the login suffix against claimed domains, and a calendar still in use."""
