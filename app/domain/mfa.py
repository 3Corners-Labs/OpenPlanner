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
password, hardware key, software key. The default of ``OIDC_MFA_AMR_VALUES``."""


def _amr_values(amr: Any) -> frozenset[str]:
    """Return the ``amr`` claim as a set of strings (a list, or one string)."""
    if isinstance(amr, str):
        return frozenset({amr})
    if isinstance(amr, list | tuple):
        return frozenset(v for v in amr if isinstance(v, str))
    return frozenset()


def shows_mfa(
    amr: Any,
    acr: Any,
    accepted_acr: Collection[str],
    accepted_amr: Collection[str] = MFA_AMR_VALUES,
) -> bool:
    """Return whether the ID-token claims show a second factor.

    Args:
        amr: The ``amr`` claim as it arrived (any type).
        acr: The ``acr`` claim as it arrived (any type).
        accepted_acr: The ``acr`` values that count as proof; any other ``acr`` does not.
        accepted_amr: The ``amr`` values that count as proof (default: the RFC 8176 ones).
    """
    if _amr_values(amr) & set(accepted_amr):
        return True
    return isinstance(acr, str) and acr in accepted_acr
