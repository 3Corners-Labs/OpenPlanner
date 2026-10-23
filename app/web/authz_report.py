"""The authorisation matrix as a report: rows for the admin page, Markdown for the docs.

``docs/authorization.md`` is generated from this module (``python -m app.cli authz-matrix``)
and a test fails when the committed file differs, so the published matrix always matches the
code (ADR 0014).
"""

from dataclasses import dataclass

from app.domain.authorization import (
    CITIZEN_ACTIONS,
    LEVELS,
    MATRIX,
    Level,
    Permission,
    Role,
)
from app.i18n import Translator
from app.web.authz_labels import (
    CITIZEN_LABELS,
    CONDITION_LABELS,
    LEVEL_LABELS,
    PERMISSION_LABELS,
    ROLE_LABELS,
)


@dataclass(frozen=True)
class MatrixRow:
    """One permission with its level and, per role, whether the role has it."""

    permission: Permission
    level: Level
    granted: dict[Role, bool]


def matrix_rows() -> list[MatrixRow]:
    """Return the matrix in a stable order: by level, then as declared."""
    order = {Level.GLOBAL: 0, Level.ORGANISATION: 1, Level.GROUP: 2}
    rows = [MatrixRow(p, LEVELS[p], {role: p in MATRIX[role] for role in Role}) for p in Permission]
    return sorted(rows, key=lambda row: order[row.level])


def render_markdown(translator: Translator, locale: str = "en") -> str:
    """Return ``docs/authorization.md``: the admin matrix and the citizen actions."""
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
        "within its scope: superuser everywhere, organisation admin in the organisations of",
        "their claim and those organisations' groups, group admin in the groups of their claim.",
        "",
        "| Permission | Level | " + " | ".join(_(ROLE_LABELS[r]) for r in roles) + " |",
        "|---|---|" + "---|" * len(roles),
    ]
    for row in matrix_rows():
        marks = " | ".join("yes" if row.granted[r] else "–" for r in roles)
        lines.append(
            f"| {_(PERMISSION_LABELS[row.permission])} (`{row.permission}`) "
            f"| {_(LEVEL_LABELS[row.level])} | {marks} |"
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
    return "\n".join(lines) + "\n"
