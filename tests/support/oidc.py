"""The admin portal as an OIDC client, for the identity provider adapter tests (ADR 0013)."""

from app.adapters.oidc import ClaimMapping
from app.domain.authorization import Role

CLIENT = "openplanner-admin"
"""The client id of the admin portal at the identity provider."""
MAPPING = ClaimMapping(
    role_claim="roles",
    organisation_claim="org",
    group_claim="groups",
    role_values={
        "op-superuser": Role.SUPERUSER,
        "op-org-admin": Role.ORG_ADMIN,
        "op-group-admin": Role.GROUP_ADMIN,
    },
)
"""Which claims carry the roles, organisations and groups, and which role values count."""
REDIRECT = "https://afspraak.example.nl/admin/callback"
"""The portal's redirect address that the identity provider sends the login back to."""
