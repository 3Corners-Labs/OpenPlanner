"""The scope filter of the top bar: organisation › group (backlog SH-3, ADR 0061 §9).

A GET form on the overview narrows what the page shows to one organisation or one group. It
only ever *keeps* rows the admin may already see: the choice is checked against the admin's own
groups (the database-side scope of the matrix) on every request, a value outside it is ignored
and never echoed, and nothing here edits anything. The choice lives in the admin session like
the theme, so the widget fragments follow the same one without carrying it in their address.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from fastapi import Request

from app.admin.authz import admin_service
from app.admin.forms import Option
from app.admin.session import remember_scope, session_scope
from app.domain.authorization import Principal
from app.domain.scheduling import GroupSummary
from app.i18n import BoundGettext

FORM_PARAM = "scope"
"""Marker field of the filter form: without it the address changes nothing."""

ORGANISATION_PARAM = "org"
GROUP_PARAM = "group"

STATE_ATTRIBUTE = "scope_choice"
"""Name on ``request.state`` that caches the choice for the rest of the request."""


@dataclass(frozen=True)
class ScopeChoice:
    """The organisation or group the overview is narrowed to; both ``None`` is everything.

    Attributes:
        organisation: Slug of the chosen organisation (always set when a group is chosen).
        group: Slug of the chosen group, if any.
    """

    organisation: str | None = None
    group: str | None = None

    @property
    def active(self) -> bool:
        """Whether anything is chosen."""
        return self.organisation is not None

    def keeps(self, organisation: str | None, group: str | None) -> bool:
        """Return whether a row of ``organisation`` and ``group`` stays in the narrowed view."""
        if self.group is not None:
            return group == self.group
        if self.organisation is not None:
            return organisation == self.organisation
        return True


def resolve(
    organisation: str | None, group: str | None, groups: Sequence[GroupSummary]
) -> ScopeChoice:
    """Check a raw choice against the admin's groups and return what is left of it.

    A group of the list wins over an organisation that does not match it; an organisation
    counts when one of the admin's groups belongs to it. Anything else is no choice at all.
    """
    by_slug = {g.group.slug: g for g in groups}
    if group in by_slug:
        return ScopeChoice(by_slug[group].organisation_slug, group)
    if organisation in {g.organisation_slug for g in groups}:
        return ScopeChoice(organisation, None)
    return ScopeChoice()


async def scope_of(
    request: Request, principal: Principal
) -> tuple[ScopeChoice, list[GroupSummary]]:
    """Return the admin's scope choice and the groups it is checked against.

    A submitted filter form (``?scope=1&org=…&group=…``) replaces the choice of the session;
    otherwise the session's choice stands. The result is cached on the request, so a page with
    several widgets reads the groups once.
    """
    cached = getattr(request.state, STATE_ATTRIBUTE, None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    groups = await admin_service(request).groups(principal)
    query = request.query_params
    submitted = query.get(FORM_PARAM) == "1"
    stored = session_scope(request)
    raw = (
        (query.get(ORGANISATION_PARAM) or None, query.get(GROUP_PARAM) or None)
        if submitted
        else stored
    )
    choice = resolve(raw[0], raw[1], groups)
    if submitted or stored != (choice.organisation, choice.group):
        remember_scope(request, choice.organisation, choice.group)
    result = (choice, groups)
    setattr(request.state, STATE_ATTRIBUTE, result)
    return result


@dataclass(frozen=True)
class Crumb:
    """One step of the chosen scope as the panel shows it: a name and where its page is."""

    label: str
    href: str | None


@dataclass(frozen=True)
class ScopeFilterView:
    """What the template draws for the filter.

    Attributes:
        summary: The chosen scope in words, for the button.
        organisations: The organisations to choose from (the first is *all*).
        groups: The groups to choose from, in the order of their organisations (first is *all*).
        organisation: Slug selected, or ``""``.
        group: Slug selected, or ``""``.
        crumbs: The chosen scope with links to the object pages; empty for everything.
    """

    summary: str
    organisations: tuple[Option, ...]
    groups: tuple[Option, ...]
    organisation: str
    group: str
    crumbs: tuple[Crumb, ...]


def filter_view(
    choice: ScopeChoice,
    groups: Sequence[GroupSummary],
    translate: BoundGettext,
    can_manage_organisations: bool,
) -> ScopeFilterView:
    """Describe the filter form for ``choice`` and the groups the admin may see.

    Args:
        choice: The checked choice.
        groups: The admin's groups.
        translate: The page's gettext.
        can_manage_organisations: Whether the admin may open organisation pages.
    """
    ordered = sorted(
        groups, key=lambda g: (g.organisation_name.casefold(), g.group.name.casefold())
    )
    organisations: dict[str, str] = {}
    for g in ordered:
        organisations.setdefault(g.organisation_slug, g.organisation_name)
    names = {g.group.slug: g.group.name for g in ordered}
    all_orgs = translate("admin.scope.all_organisations")
    all_groups = translate("admin.scope.all_groups")
    crumbs: list[Crumb] = []
    if choice.organisation is not None:
        href = f"/admin/organisations/{choice.organisation}" if can_manage_organisations else None
        crumbs.append(Crumb(organisations[choice.organisation], href))
    if choice.group is not None:
        crumbs.append(Crumb(names[choice.group], f"/admin/groups/{choice.group}"))
    summary = " › ".join(
        [
            organisations[choice.organisation] if choice.organisation else all_orgs,
            names[choice.group] if choice.group else all_groups,
        ]
    )
    return ScopeFilterView(
        summary,
        (Option("", all_orgs), *(Option(slug, name) for slug, name in organisations.items())),
        (Option("", all_groups), *(Option(slug, name) for slug, name in names.items())),
        choice.organisation or "",
        choice.group or "",
        tuple(crumbs),
    )
