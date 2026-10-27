"""The authorisation matrix as a report: rows for the admin page, Markdown for the docs.

``docs/authorization.md`` is generated from this module (``python -m app.cli authz-matrix``)
and a test fails when the committed file differs, so the published matrix always matches the
code (ADR 0014).
"""

from dataclasses import dataclass

from app.domain.authorization import (
    API_SCOPES,
    CITIZEN_ACTIONS,
    LEVELS,
    LOCKED_PERMISSIONS,
    MATRIX,
    PII_PERMISSIONS,
    Level,
    Permission,
    Role,
)
from app.i18n import Translator
from app.web.authz_labels import (
    API_SCOPE_LABELS,
    CITIZEN_LABELS,
    CONDITION_LABELS,
    LEVEL_LABELS,
    PERMISSION_LABELS,
    ROLE_LABELS,
)


@dataclass(frozen=True)
class MatrixRow:
    """One permission with its level, its bounds (ADR 0037) and, per role, whether it is held."""

    permission: Permission
    level: Level
    granted: dict[Role, bool]
    locked: bool
    pii: bool


def matrix_rows() -> list[MatrixRow]:
    """Return the matrix in a stable order: by level, then as declared."""
    order = {Level.GLOBAL: 0, Level.ORGANISATION: 1, Level.GROUP: 2}
    rows = [
        MatrixRow(
            p,
            LEVELS[p],
            {role: p in MATRIX[role] for role in Role},
            locked=p in LOCKED_PERMISSIONS,
            pii=p in PII_PERMISSIONS,
        )
        for p in Permission
    ]
    return sorted(rows, key=lambda row: order[row.level])


def render_markdown(translator: Translator, locale: str = "en") -> str:
    """Return ``docs/authorization.md``: the admin matrix, citizen actions and API scopes."""
    _ = translator.bind(locale)
    roles = list(Role)
    lines = [
        "# Authorisation matrix",
        "",
        "Generated from `app/domain/authorization.py` by `python -m app.cli authz-matrix`.",
        "Do not edit by hand: CI fails when this file differs from the code (ADR 0014).",
        "",
        "## Admins",
        "",
        "Roles come from the identity provider (ADR 0013). A role grants a permission only",
        "within its reach: superuser everywhere; organisation admin and auditor in the",
        "organisations of their claim and those organisations' groups; group admin in the",
        "groups of their claim. The auditor reads only and never sees personal data (ADR 0037).",
        "",
        "Bounds (ADR 0037): *locked* permissions belong to the superuser only; *personal data*",
        "permissions show citizens' data and are never held by the auditor.",
        "",
        "| Permission | Level | Bounds | " + " | ".join(_(ROLE_LABELS[r]) for r in roles) + " |",
        "|---|---|---|" + "---|" * len(roles),
    ]
    for row in matrix_rows():
        marks = " | ".join("yes" if row.granted[r] else "–" for r in roles)
        bounds = ", ".join(
            label for label, on in (("locked", row.locked), ("personal data", row.pii)) if on
        )
        lines.append(
            f"| {_(PERMISSION_LABELS[row.permission])} (`{row.permission}`) "
            f"| {_(LEVEL_LABELS[row.level])} | {bounds or '–'} | {marks} |"
        )
    lines += [
        "",
        "## Citizens",
        "",
        "Citizens never log in. Their rights depend on the management link from their",
        "confirmation email and on the change deadline (the group's dispatch lead).",
        "",
        "| Action | Condition |",
        "|---|---|",
    ]
    for action in CITIZEN_ACTIONS:
        lines.append(
            f"| {_(CITIZEN_LABELS[action.key])} (`{action.key}`) "
            f"| {_(CONDITION_LABELS[action.condition])} |"
        )
    lines += [
        "",
        "## Machines (API clients)",
        "",
        "Contact-center bots never get a role (ADR 0023). The superuser registers each one as",
        "an API client of one organisation, optionally narrowed to some of its groups, with",
        "the scopes below. There is no superuser equivalent for machines.",
        "",
        "| Scope | Level |",
        "|---|---|",
    ]
    for scope, level in API_SCOPES.items():
        lines.append(f"| {_(API_SCOPE_LABELS[scope])} (`{scope}`) | {_(LEVEL_LABELS[level])} |")
    return "\n".join(lines) + "\n"
