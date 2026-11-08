"""What the *Kanalen* page's cards share: the permission, the page address and the inputs.

The mail, SMS and contact-center cards of an organisation (ADR 0022, ADR 0032 point 9) live on
one page under one permission; their write-only secret inputs render the same way.
"""

from typing import Annotated

from fastapi import Depends

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import Field, FormData
from app.domain.authorization import Permission

Manage = Annotated[
    AdminContext,
    Depends(
        RequirePermission(Permission.INTEGRATIONS_MANAGE, organisation_param="org", fresh=True)
    ),
]
"""The permission of every channel page of one organisation."""


def channels_url(org: str) -> str:
    """Return the channels page of organisation ``org``."""
    return f"/admin/organisations/{org}/channels"


def value_of(form: FormData, name: str, stored: object) -> str:
    """Return the submitted value of ``name``, else the stored one ('' for none)."""
    if name in form.values:
        return form.values[name]
    return "" if stored is None else str(stored)


def secret_field(name: str, label: str, hint: str, form: FormData, required: bool) -> Field:
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
