"""Alert targets in the version-1 configuration document (ADR 0099 point 3, ADR 0082 point 9).

Where alerts go: an email address or a Webex room, with the least urgent level it gets. They
sit on the platform and on every organisation. A target is known by its kind and address within
its owner, so a document writes them as ``{"email": {"ops@voorbeeld.nl": "warning"}}``. The kind
checks build the ``AlertTarget`` the alert screens build; the rule across objects is the limit
per owner.
"""

from collections.abc import Iterable, Mapping

from app.domain.alert_delivery import MAX_TARGETS
from app.domain.alert_model import AlertChannelKind, AlertTarget
from app.domain.authorization import Permission
from app.domain.configuration_changes import (
    ChangeAction,
    IntegrityRule,
    Refusal,
    Resolution,
    RuleContext,
)
from app.domain.configuration_schema import Collection, Kind, Shape, text
from app.domain.configuration_tree import walk
from app.domain.configuration_values import Value
from app.domain.errors import DomainError, ErrorCode

_KINDS = tuple(kind.value for kind in AlertChannelKind)
_LEVELS = ("warning", "outage")


def _check_target(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check a target as the alert screens do: an address that fits its kind, a known level."""
    try:
        AlertTarget(
            None,
            None,
            AlertChannelKind(str(merged["kind"])),
            str(merged["address"]),
            str(merged["min_level"]),
        )
    except ValueError:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "kind") from None
    return merged


def _kind(name: str, permission: Permission) -> Kind:
    """Return the kind of an alert target of one owner (the platform or an organisation)."""
    return Kind(
        name,
        key=("kind", "address"),
        permission=permission,
        tables=frozenset({"alert_targets"}),
        fields=(
            text("kind", required=True, choices=_KINDS),
            text("address", required=True, max_length=320),
            text("min_level", choices=_LEVELS, default="warning"),
        ),
        check=_check_target,
        description="Where alerts go: an email address or a Webex room, and the least level.",
    )


PLATFORM_ALERT_TARGET = _kind("platform_alert_target", Permission.SETTINGS_PLATFORM_MANAGE)
ORGANISATION_ALERT_TARGET = _kind("organisation_alert_target", Permission.INTEGRATIONS_MANAGE)

PLATFORM_ALERT_TARGETS = Collection("alert_targets", PLATFORM_ALERT_TARGET, Shape.MAP)
ORGANISATION_ALERT_TARGETS = Collection("alert_targets", ORGANISATION_ALERT_TARGET, Shape.MAP)


def _limit(resolution: Resolution) -> Iterable[Refusal]:
    """Refuse an owner that would hold more than ``MAX_TARGETS`` targets after a plan adds one."""
    added = {
        (change.kind, change.address[: -len(change.key)])
        for change in resolution.changes
        if change.action is ChangeAction.CREATE
        and change.kind in {PLATFORM_ALERT_TARGET.name, ORGANISATION_ALERT_TARGET.name}
    }
    for kind, owner in sorted(added):
        count = sum(
            1
            for address, found in walk(resolution.resulting, kind)
            if address[: -len(found.key)] == owner
        )
        if count > MAX_TARGETS:
            path = (
                "platform.alert_targets"
                if not owner
                else f"organisations[{owner[0]}].alert_targets"
            )
            yield Refusal(path, ErrorCode.INVALID_CONFIGURATION, "targets")


INTEGRITY: tuple[IntegrityRule, ...] = (_limit,)
