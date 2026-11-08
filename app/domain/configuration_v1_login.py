"""Identity providers in a configuration document: providers, signing keys, login domains.

The organisation's logins of ADR 0083 as ADR 0099 states them. A provider is addressed by its
key; the signing keys it signs with (kid) and the e-mail domains it claims (domain) are its
children. The rules are the ones the admin screens apply: ``ProviderDraft.create`` for the
fields, the secret rules of the client authentication, and across objects the uniqueness of
provider keys and domains, the number of providers per organisation and of domains per
provider, and the reserved suffix of the bundled provider.

What a document never holds, because the adapter derives it: the issuer and the check time
(the adapter fetches the discovery document when it creates a provider and when its discovery
URL or client authentication changes; the domain layer does not fetch), the timestamps of a
registration, and the verification state of a domain (a new domain starts pending with a new
token, so a document can never carry a login domain from one installation to another as
verified).
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from app.domain.authorization import Permission
from app.domain.configuration_changes import IntegrityRule, Refusal, Resolution, RuleContext
from app.domain.configuration_schema import (
    Collection,
    Kind,
    Shape,
    boolean,
    json_object,
    secret,
    text,
)
from app.domain.configuration_tree import Address, Entity, walk
from app.domain.configuration_values import Secret, Value
from app.domain.errors import DomainError, ErrorCode
from app.domain.identity_providers import (
    DEFAULT_SCOPES,
    MAX_CLIENT_ID_LENGTH,
    MAX_NAME_LENGTH,
    MAX_URL_LENGTH,
    ClientAuth,
    ProviderDraft,
    validate_secret,
)
from app.domain.login_domains import (
    MAX_DOMAIN_LENGTH,
    MAX_DOMAINS_PER_PROVIDER,
    normalise_domain,
    reserved,
)
from app.domain.mfa import MfaPolicy

PROVIDER_KIND = "identity_provider"
KEY_KIND = "identity_provider_key"
DOMAIN_KIND = "identity_provider_domain"
"""The kind names, which other slices read by name."""

DEFAULT_MAX_PER_ORGANISATION = 2
"""``idp_max_per_organisation`` when the context does not carry it (the setting's default)."""

MAX_KID_LENGTH = 64
MAX_MFA_LENGTH = 1100


def _invalid(field: str) -> DomainError:
    """Return the refusal for ``field``."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, field)


def _str(values: Mapping[str, Value], name: str) -> str:
    """Return a text field, or the empty text when it is not one."""
    value = values.get(name)
    return value if isinstance(value, str) else ""


def _provider_check(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check a provider as the admin screen does and return its normalised fields.

    A client secret is needed with ``client_secret_basic`` (given now or already stored) and
    refused with ``private_key_jwt`` when the document gives a new one.

    Raises:
        DomainError: ``invalid_configuration`` naming the field.
    """
    draft = ProviderDraft.create(
        key=_str(merged, "key"),
        name=_str(merged, "name"),
        discovery_url=_str(merged, "discovery_url"),
        client_id=_str(merged, "client_id"),
        client_auth=_str(merged, "client_auth"),
        scopes=_str(merged, "scopes"),
        entitlement_claim=_str(merged, "entitlement_claim"),
        entitlement_prefix=_str(merged, "entitlement_prefix"),
        mfa_policy=_str(merged, "mfa_policy"),
        mfa_amr_values=_str(merged, "mfa_amr_values"),
        mfa_acr_values=_str(merged, "mfa_acr_values"),
        allow_http=not context.production,
    )
    stored = merged.get("client_secret")
    given = stored is not None and (current is None or stored != current.get("client_secret"))
    if draft.client_auth.needs_secret:
        if stored is None:
            raise _invalid("client_secret")
        if isinstance(stored, Secret):
            validate_secret(stored.reveal())
    elif given:
        raise _invalid("client_secret")
    return {
        **merged,
        "name": draft.name,
        "discovery_url": draft.discovery_url,
        "client_id": draft.client_id,
        "client_auth": draft.client_auth.value,
        "scopes": draft.scopes,
        "entitlement_claim": draft.entitlement_claim,
        "entitlement_prefix": draft.entitlement_prefix,
        "mfa_policy": draft.mfa_policy.value,
        "mfa_amr_values": draft.mfa_amr_values,
        "mfa_acr_values": draft.mfa_acr_values,
    }


def _moment(values: Mapping[str, Value], name: str) -> str | None:
    """Return the time field as UTC ISO text, ``None`` when absent; refuse anything else."""
    value = values.get(name)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        raise _invalid(name) from None
    if parsed.tzinfo is None:
        raise _invalid(name)
    return parsed.astimezone(UTC).isoformat()


def _signing_key_check(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check a signing key: a public JWK of texts, UTC times, and a private key on create.

    Raises:
        DomainError: ``invalid_configuration`` naming the field.
    """
    jwk = merged.get("public_jwk")
    if not isinstance(jwk, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in jwk.items()
    ):
        raise _invalid("public_jwk")
    if current is None and merged.get("private_key") is None:
        raise _invalid("private_key")
    created, retired = _moment(merged, "created_at"), _moment(merged, "retired_at")
    return {**merged, "created_at": created, "retired_at": retired}


def _domain_check(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Refuse a domain that is not written the normalised way (lower case, ASCII).

    Raises:
        DomainError: ``idp_domain_invalid`` with ``domain``.
    """
    domain = _str(merged, "domain")
    if normalise_domain(domain) != domain:
        raise DomainError(ErrorCode.IDP_DOMAIN_INVALID, "domain")
    return merged


DOMAIN = Kind(
    DOMAIN_KIND,
    key=("domain",),
    tables=frozenset({"identity_provider_domains"}),
    fields=(text("domain", required=True, max_length=MAX_DOMAIN_LENGTH),),
    check=_domain_check,
    derived=frozenset(
        {
            "identity_provider_domains.token",
            "identity_provider_domains.state",
            "identity_provider_domains.created_at",
            "identity_provider_domains.verified_at",
            "identity_provider_domains.checked_at",
            "identity_provider_domains.missing_since",
        }
    ),
    description="An e-mail domain the provider claims; its DNS verification is the installation's.",
)

SIGNING_KEY = Kind(
    KEY_KIND,
    key=("kid",),
    tables=frozenset({"identity_provider_keys"}),
    fields=(
        text("kid", required=True, max_length=MAX_KID_LENGTH),
        json_object("public_jwk", description="The public key as a JWK (texts only)."),
        secret("private_key", columns=("key_nonce", "key_ciphertext")),
        text("created_at", description="When the key was generated (ISO 8601, with zone)."),
        text("retired_at", nullable=True, description="When a rotation retired it."),
    ),
    check=_signing_key_check,
    description="A signing key pair of a private_key_jwt provider; only with secrets.",
)

PROVIDER = Kind(
    PROVIDER_KIND,
    key=("key",),
    permission=Permission.IDENTITY_PROVIDERS_MANAGE,
    tables=frozenset({"identity_providers"}),
    fields=(
        text("key", required=True),
        text("name", max_length=MAX_NAME_LENGTH, required=True),
        text("discovery_url", max_length=MAX_URL_LENGTH, required=True),
        text("client_id", max_length=MAX_CLIENT_ID_LENGTH, required=True),
        text("client_auth", choices=tuple(a.value for a in ClientAuth), required=True),
        text("scopes", default=DEFAULT_SCOPES),
        text("entitlement_claim", required=True),
        text("entitlement_prefix", required=True),
        boolean("enabled", default=False),
        text(
            "mfa_policy", choices=tuple(p.value for p in MfaPolicy), default=MfaPolicy.RECORD.value
        ),
        text("mfa_amr_values", max_length=MAX_MFA_LENGTH, default=""),
        text("mfa_acr_values", max_length=MAX_MFA_LENGTH, default=""),
        secret("client_secret", columns=("secret_nonce", "secret_ciphertext")),
    ),
    collections=(
        Collection("keys", SIGNING_KEY, Shape.ENTITIES),
        Collection("domains", DOMAIN, Shape.KEYS),
    ),
    check=_provider_check,
    derived=frozenset(
        {
            "identity_providers.issuer",
            "identity_providers.created_at",
            "identity_providers.updated_at",
            "identity_providers.checked_at",
        }
    ),
    description="An organisation's OpenID Connect provider (ADR 0083).",
)

IDENTITY_PROVIDERS = Collection(
    "identity_providers", PROVIDER, Shape.ENTITIES, "The organisation's identity providers."
)
"""The ``identity_providers`` section of an organisation."""


def _known_addresses(resolution: Resolution, kind: str) -> set[Address]:
    """Return the addresses of the objects of ``kind`` the installation has now."""
    return {address for address, _ in walk(resolution.current, kind)}


def _provider_keys_unique(resolution: Resolution) -> Iterable[Refusal]:
    """Refuse a provider key that two organisations would both hold (one key, one provider)."""
    found: dict[str, list[Entity]] = defaultdict(list)
    for address, entity in walk(resolution.resulting, PROVIDER_KIND):
        found[address[-1]].append(entity)
    for entities in found.values():
        if len(entities) > 1:
            for entity in entities:
                if entity.path:
                    yield Refusal(entity.path, ErrorCode.IDP_KEY_TAKEN, "key")


def _providers_per_organisation(resolution: Resolution) -> Iterable[Refusal]:
    """Refuse a new provider beyond ``idp_max_per_organisation`` (default 2) of its organisation."""
    limit = resolution.context.settings.get(
        "idp_max_per_organisation", DEFAULT_MAX_PER_ORGANISATION
    )
    maximum = limit if isinstance(limit, int) else DEFAULT_MAX_PER_ORGANISATION
    known = _known_addresses(resolution, PROVIDER_KIND)
    counted: dict[str, int] = defaultdict(int)
    for address, entity in walk(resolution.resulting, PROVIDER_KIND):
        counted[address[0]] += 1
        if address not in known and entity.path and counted[address[0]] > maximum:
            yield Refusal(entity.path, ErrorCode.IDENTITY_PROVIDER_LIMIT_REACHED)


def _domains(resolution: Resolution) -> Iterable[Refusal]:
    """Refuse a new login domain that is reserved, claimed twice or beyond the limit.

    The reserved suffix is the platform's ``bundled_idp_login_suffix``; the limit is
    ``MAX_DOMAINS_PER_PROVIDER`` (ADR 0083 point 11). Only domains this plan adds are refused.
    """
    suffix = resolution.context.settings.get("bundled_idp_login_suffix")
    known = _known_addresses(resolution, DOMAIN_KIND)
    owners: dict[str, int] = defaultdict(int)
    per_provider: dict[Address, int] = defaultdict(int)
    for address, _ in walk(resolution.resulting, DOMAIN_KIND):
        owners[address[-1]] += 1
    for address, entity in walk(resolution.resulting, DOMAIN_KIND):
        domain, provider = address[-1], address[:-1]
        per_provider[provider] += 1
        if address in known or not entity.path:
            continue
        code = None
        if isinstance(suffix, str) and suffix and reserved(domain, suffix):
            code = ErrorCode.IDP_DOMAIN_RESERVED
        elif owners[domain] > 1:
            code = ErrorCode.IDP_DOMAIN_TAKEN
        elif per_provider[provider] > MAX_DOMAINS_PER_PROVIDER:
            code = ErrorCode.IDP_DOMAIN_LIMIT
        if code is not None:
            yield Refusal(entity.path, code, "domain")


INTEGRITY: tuple[IntegrityRule, ...] = (
    _provider_keys_unique,
    _providers_per_organisation,
    _domains,
)
"""The rules across objects of the identity providers."""
