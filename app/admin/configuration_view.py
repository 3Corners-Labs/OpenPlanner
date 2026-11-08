"""The plan of a configuration document as the plan page shows it (ADR 0099 point 13).

The page lists what applying would do, object by object, with the names of the fields and
never a value, and the refusals that stop it. It adds the warnings an admin should read before
pressing apply: deletions, a changed claim value (who matches an entitlement), a replaced Webex
Service App token (the first installation's copy stops working). Texts come from the catalogue;
the kinds and fields are the document's own words, which the admin edits in their file.
"""

from dataclasses import dataclass

from app.admin.forms import CODE_MESSAGES
from app.config import Settings
from app.domain.configuration_changes import ChangeAction
from app.domain.configuration_document import Scope
from app.domain.configuration_package import MIN_PASSPHRASE_LENGTH
from app.domain.configuration_plan import Plan
from app.domain.configuration_v1_names import PLATFORM_SECRET_KIND
from app.domain.configuration_v1_platform import WEBEX_SERVICE_APP
from app.i18n import BoundGettext

DOCUMENT_KEY = "document"
"""The error key of the document block (a refusal of an export, a plan or an apply)."""
PLATFORM_HOME = "/admin/settings/package"
"""The platform's page of the block: the package tab of Advanced settings."""
PLATFORM_PREFIX = "/admin/settings/configuration"
"""Where the platform's export, plan and apply are posted."""

CLAIM_FIELDS = frozenset({"idp_key", "claim_value"})
"""The fields that decide who matches an entitlement: a change locks people out until the
identity provider sends the new value."""

ACTION_LABELS = {
    ChangeAction.CREATE: "admin.configuration.action.create",
    ChangeAction.UPDATE: "admin.configuration.action.update",
    ChangeAction.DELETE: "admin.configuration.action.delete",
}


@dataclass(frozen=True)
class ChangeRow:
    """One line of the plan table."""

    action: str
    label: str
    kind: str
    path: str
    fields: str


@dataclass(frozen=True)
class RefusalRow:
    """One refusal: where it is, which field, and why in words."""

    path: str
    field: str
    message: str


@dataclass(frozen=True)
class PlanView:
    """What the plan page shows.

    Attributes:
        changes: The table.
        refusals: What stops the plan.
        warnings: Catalogue keys of the warnings that apply, in order.
        deletes: How many objects are deleted.
        plan_hash: The hash the apply must carry.
        can_apply: Whether there is something to apply and nothing refuses it.
    """

    changes: tuple[ChangeRow, ...]
    refusals: tuple[RefusalRow, ...]
    warnings: tuple[str, ...]
    deletes: int
    plan_hash: str
    can_apply: bool


def plan_view(plan: Plan, _: BoundGettext) -> PlanView:
    """Describe ``plan`` for the page."""
    changes = tuple(
        ChangeRow(
            change.action.value,
            _(ACTION_LABELS[change.action]),
            change.kind,
            change.path,
            ", ".join(change.fields) if change.action is not ChangeAction.DELETE else "",
        )
        for change in plan.changes
    )
    refusals = tuple(
        RefusalRow(r.path, r.field or "", _(CODE_MESSAGES.get(r.code, "error.invalid_request")))
        for r in plan.refusals
    )
    deletes = sum(1 for c in plan.changes if c.action is ChangeAction.DELETE)
    warnings: list[str] = []
    if deletes:
        warnings.append("admin.configuration.warning.deletes")
    if any(
        CLAIM_FIELDS & set(c.fields) for c in plan.changes if c.action is not ChangeAction.DELETE
    ):
        warnings.append("admin.configuration.warning.claims")
    if any(c.kind == PLATFORM_SECRET_KIND and c.key == (WEBEX_SERVICE_APP,) for c in plan.changes):
        warnings.append("admin.configuration.warning.webex")
    return PlanView(
        changes, refusals, tuple(warnings), deletes, plan.digest, bool(changes) and plan.ok
    )


def organisation_home(org: str) -> str:
    """Return the page of organisation ``org``'s configuration block."""
    return f"/admin/organisations/{org}/configuration"


@dataclass(frozen=True)
class DocumentBlock:
    """What the export and upload forms of the block need.

    Attributes:
        prefix: Where the forms post (``/export``, ``/plan`` and ``/apply`` follow).
        home: The page the block sits on, where an apply returns to.
        error: The refusal in words, if the last action was refused.
        min_length: The shortest passphrase a sealed export takes.
        max_mb: The largest document accepted.
        platform: Whether this is the platform's block (else an organisation's).
        can_seal: Whether the admin may export with secrets (platform rights only).
    """

    prefix: str
    home: str
    error: str | None
    min_length: int
    max_mb: int
    platform: bool
    can_seal: bool


def document_block(
    settings: Settings, scope: Scope, *, can_seal: bool, error: str | None = None
) -> DocumentBlock:
    """Return the block for ``scope``; the one place that tells the two scopes apart."""
    home = PLATFORM_HOME if scope.is_platform else organisation_home(str(scope.organisation))
    return DocumentBlock(
        PLATFORM_PREFIX if scope.is_platform else home,
        home,
        error,
        MIN_PASSPHRASE_LENGTH,
        settings.configuration_package_max_mb,
        scope.is_platform,
        can_seal,
    )
