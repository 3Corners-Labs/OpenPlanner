"""Proof of multi-factor authentication in an ID token (ADR 0100).

MFA itself is the identity provider's job. OpenPlanner only reads the proof the provider puts
in the ID token: the ``amr`` claim (RFC 8176) or the ``acr`` claim (OIDC Core §2) that
answers the ``acr_values`` OpenPlanner asked for. Pure Python; the adapter passes the claims.
"""

from collections.abc import Collection
from typing import Any

MFA_REQUIRED = "mfa_required"
"""The reason code of a login whose ID token shows no second factor (``IdentityError.code``)."""

MFA_AMR_VALUES = frozenset({"mfa", "otp", "hwk", "swk"})
"""The ``amr`` values (RFC 8176) that show a second factor: multiple-factor, one-time
password, hardware key, software key. A fixed rule, not a setting."""


def _amr_values(amr: Any) -> frozenset[str]:
    """Return the ``amr`` claim as a set of strings (a list, or one string)."""
    if isinstance(amr, str):
        return frozenset({amr})
    if isinstance(amr, list | tuple):
        return frozenset(v for v in amr if isinstance(v, str))
    return frozenset()


def shows_mfa(amr: Any, acr: Any, requested_acr: Collection[str]) -> bool:
    """Return whether the ID-token claims show a second factor.

    Args:
        amr: The ``amr`` claim as it arrived (any type).
        acr: The ``acr`` claim as it arrived (any type).
        requested_acr: The ``acr_values`` OpenPlanner asked for; an ``acr`` that is one of
            them counts as proof, any other ``acr`` does not.
    """
    if _amr_values(amr) & MFA_AMR_VALUES:
        return True
    return isinstance(acr, str) and acr in requested_acr
