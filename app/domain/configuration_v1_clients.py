"""The API client in a version-1 configuration document (ADR 0099).

A client of the machine API (ADR 0023) belongs to one organisation and may be narrowed to some
of its groups. Its secret is issued once by the portal and only its SHA-256 is stored, so a
document carries that hash sealed like any secret and can never create a client without one;
it can move an existing client between installations, and change its name, scopes, networks,
groups and switch. The rules are those of the client screen: ``ClientDraft.create`` for name,
scopes and networks, the organisation and its groups checked against what the document leaves
behind, and an enabled client is not deleted.
"""

import re
from collections.abc import Iterator, Mapping
from datetime import datetime, timedelta
from typing import cast

from app.domain.api_clients import MAX_NAME_LENGTH, ClientDraft, is_client_id
from app.domain.authorization import Permission
from app.domain.configuration_changes import (
    ChangeAction,
    IntegrityRule,
    Refusal,
    Resolution,
    RuleContext,
)
from app.domain.configuration_checks import invalid, strings_of, utc_moment
from app.domain.configuration_schema import Kind, boolean, secret, strings, text
from app.domain.configuration_tree import Entity, walk
from app.domain.configuration_v1_names import API_CLIENT_KIND, GROUP_KIND, ORGANISATION_KIND
from app.domain.configuration_values import Json, Secret, Value
from app.domain.errors import ErrorCode

_HASH = re.compile(r"[0-9a-f]{64}")


def _hash_check(value: Value, context: RuleContext) -> Value:
    """Check the stored secret hash: 64 lower-case hex characters, once it is opened.

    A secret still sealed is left to the adapter, which opens it before the plan is made.

    Raises:
        DomainError: ``invalid_configuration`` naming ``secret_hash``.
    """
    if isinstance(value, Secret) and not _HASH.fullmatch(value.reveal()):
        raise invalid("secret_hash")
    return value


MAX_SECRET_DAYS = 365
"""The longest a client's secret lives: the longest lifetime the portal offers (ADR 0023)."""


def _expiry_check(value: Value, context: RuleContext) -> Value:
    """Check when the secret expires: an ISO date and time with an offset, at most a year away.

    Returned in UTC. The portal issues a secret for 90, 180 or 365 days; a document may carry
    an earlier expiry (an export) but never one beyond that.

    Raises:
        DomainError: ``invalid_configuration`` naming ``secret_expires_at``.
    """
    text_value = utc_moment(value, "secret_expires_at")
    if datetime.fromisoformat(text_value) > context.now + timedelta(days=MAX_SECRET_DAYS):
        raise invalid("secret_expires_at")
    return text_value


def _check_client(
    view: Mapping[str, Value], current: Mapping[str, Value] | None, ctx: RuleContext
) -> Mapping[str, Value]:
    """Check a whole client as the client screen does; return it normalised.

    A new client must carry its sealed secret hash and when it expires: a secret is issued
    once by the portal, never by a document.
    """
    if not is_client_id(str(view["client_id"])):
        raise invalid("client_id")
    if current is None:
        for name in ("secret_hash", "secret_expires_at"):
            if view.get(name) is None:
                raise invalid(name)
        if datetime.fromisoformat(str(view["secret_expires_at"])) <= ctx.now:
            raise invalid("secret_expires_at")
    draft = ClientDraft.create(
        name=str(view["name"]),
        organisation=str(view["organisation"]),
        groups=strings_of(view, "groups"),
        scopes=strings_of(view, "scopes"),
        networks=strings_of(view, "networks"),
    )
    return {
        **view,
        "name": draft.name,
        "scopes": cast("list[Json]", sorted(scope.value for scope in draft.scopes)),
        "networks": cast("list[Json]", list(draft.networks)),
        "groups": cast("list[Json]", sorted(draft.groups)),
    }


API_CLIENT = Kind(
    API_CLIENT_KIND,
    key=("client_id",),
    permission=Permission.API_CLIENTS_MANAGE,
    tables=frozenset({"api_clients", "api_client_groups"}),
    organisation_field="organisation",
    fields=(
        text("client_id", required=True, max_length=80),
        text("name", required=True, max_length=MAX_NAME_LENGTH),
        text("organisation", required=True, max_length=64),
        strings("scopes"),
        strings("networks"),
        boolean("enabled", default=False),
        strings("groups", columns=("api_client_groups.group_id",)),
        secret("secret_hash", check=_hash_check),
        text("secret_expires_at", nullable=True, check=_expiry_check),
    ),
    check=_check_client,
    derived=frozenset({"api_clients.created_at", "api_clients.last_token_at"}),
    description="A bot of the machine API: organisation, groups, scopes and sealed secret hash.",
)


def _organisation_rules(resolution: Resolution) -> Iterator[Refusal]:
    """A client's organisation exists and its groups are groups of that organisation."""
    organisations = {address[0] for address, _ in walk(resolution.resulting, ORGANISATION_KIND)}
    groups: dict[str, set[str]] = {}
    for address, _ in walk(resolution.resulting, GROUP_KIND):
        groups.setdefault(address[0], set()).add(address[-1])
    for address, client in walk(resolution.resulting, API_CLIENT.name):
        path = f"api_clients[{address[0]}]"
        organisation = str(client.fields.get("organisation"))
        if organisation not in organisations:
            yield Refusal(path, ErrorCode.ORGANISATION_NOT_FOUND, "organisation")
        elif not set(strings_of(client.fields, "groups")) <= groups.get(organisation, set()):
            yield Refusal(path, ErrorCode.INVALID_CONFIGURATION, "groups")


def _enabled_rules(resolution: Resolution) -> Iterator[Refusal]:
    """A client is deleted only after it was disabled."""
    before: dict[str, Entity] = {
        address[0]: client for address, client in walk(resolution.current, API_CLIENT.name)
    }
    for change in resolution.deletions:
        old = before.get(change.key[0]) if change.kind == API_CLIENT.name else None
        if change.action is ChangeAction.DELETE and old is not None and old.fields.get("enabled"):
            yield Refusal(change.path, ErrorCode.API_CLIENT_ENABLED)


INTEGRITY: tuple[IntegrityRule, ...] = (_organisation_rules, _enabled_rules)
"""The rules across objects that concern API clients."""
