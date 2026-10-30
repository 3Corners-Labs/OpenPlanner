"""The OIDC login mapping as settings: which claims and values make an admin (ADR 0042 §8).

Eight class C settings, all under ``settings.login.manage``: the claims that carry roles,
organisations and groups, and the role value of each built-in role. A new mapping is saved only
after a dry run on the superuser's own fresh login (``SettingsService.change_login_mapping``);
this module checks the values before that run.
"""

import re
from collections.abc import Mapping

from app.domain.errors import DomainError, ErrorCode

CLAIM_KEYS = ("oidc_role_claim", "oidc_organisation_claim", "oidc_group_claim")
"""The settings that name a claim of the ID token."""

ROLE_KEYS = (
    "oidc_role_superuser",
    "oidc_role_org_admin",
    "oidc_role_group_admin",
    "oidc_role_auditor",
    "oidc_role_agent",
)
"""The settings that hold the role claim's value for each built-in role."""

LOGIN_KEYS = CLAIM_KEYS + ROLE_KEYS
"""Every setting of the login mapping, in form order."""

_TOKEN = re.compile(r"[A-Za-z0-9_.:/@-]{1,128}")
"""A claim name or role value: no spaces, separators of a claim list, or markup."""


def validate_mapping(values: Mapping[str, str]) -> dict[str, str]:
    """Return the eight values trimmed, or refuse the first one that is not usable.

    Raises:
        DomainError: ``setting_invalid`` with the key as field: a value missing, not a plain
            token, or a role value used for two roles (one login would then hold both).
    """
    found: dict[str, str] = {}
    for key in LOGIN_KEYS:
        value = str(values.get(key, "")).strip()
        if not _TOKEN.fullmatch(value):
            raise DomainError(ErrorCode.SETTING_INVALID, key)
        found[key] = value
    seen: set[str] = set()
    for key in ROLE_KEYS:
        if found[key] in seen:
            raise DomainError(ErrorCode.SETTING_INVALID, key)
        seen.add(found[key])
    return found
