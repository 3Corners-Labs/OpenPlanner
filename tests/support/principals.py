"""Admins for tests: build a ``Principal`` from roles and targets, or legacy claims from one.

``principal`` spells out the grants of ADR 0049 the way the three legacy claims used to:
every organisation-reach role on every organisation, every group-reach role on every group
(with its organisation from ``GROUP_ORGANISATIONS``, the slug itself when unknown).
``legacy_claims`` is the reverse, for the fake identity provider.
"""

from collections.abc import Iterable, Mapping

from app.domain.authorization import REACH, Grant, Principal, Reach, Role
from app.domain.entitlements import LoginClaims

GROUP_ORGANISATIONS: Mapping[str, str] = {
    "demo": "demo",
    "team": "demo",
    "elders": "demo",
    "andere-team": "andere",
    "demo-gesloten": "demo",
    "demo-gepland": "demo",
}
"""The organisation of the group slugs the fixtures use."""


def principal(
    subject: str,
    name: str,
    roles: Iterable[Role] = (),
    organisations: Iterable[str] = (),
    groups: Iterable[str | tuple[str, str]] = (),
    *,
    grants: Iterable[Grant] = (),
) -> Principal:
    """Return an admin holding ``roles`` on ``organisations`` and ``groups`` (plus ``grants``).

    A group is a slug (its organisation from ``GROUP_ORGANISATIONS``) or an
    ``(organisation, group)`` pair.
    """
    found: set[Grant] = set(grants)
    for role in roles:
        reach = REACH[role]
        if reach is Reach.EVERYTHING:
            found.add(Grant(role.value))
        elif reach is Reach.ORGANISATIONS:
            found |= {Grant(role.value, slug) for slug in organisations}
        else:
            for group in groups:
                organisation, slug = (
                    group
                    if isinstance(group, tuple)
                    else (GROUP_ORGANISATIONS.get(group, group), group)
                )
                found.add(Grant(role.value, organisation, slug))
    return Principal(subject, name, frozenset(found))


def legacy_claims(admin: Principal, role_claims: Iterable[str] = ()) -> LoginClaims:
    """Return the three-claim login that resolves to ``admin`` in legacy mode."""
    reach = {role.value: REACH[role] for role in Role}
    return LoginClaims(
        subject=admin.subject,
        display_name=admin.display_name,
        roles=admin.roles,
        organisations=frozenset(
            g.organisation
            for g in admin.grants
            if g.organisation and reach.get(g.role) is Reach.ORGANISATIONS
        ),
        groups=frozenset(
            g.group for g in admin.grants if g.group and reach.get(g.role) is Reach.GROUPS
        ),
        role_claims=frozenset(role_claims) | frozenset(r.claim_value for r in admin.custom_roles),
    )
