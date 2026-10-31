"""Everything an admin can create in the portal can be changed and deleted (ADR 0084).

The product owner found no way to delete an organisation or a group. The rule of ADR 0084
point 1 says that a create route never ships without its change and delete (or retire) routes,
unless the decision names the exception. This test is point 8 of that ADR: it finds every
create route in the admin routers by its handler name, requires each one to be registered
below with its change and delete routes, and checks that those routes exist. A gap that the
ADR allows, or that its build steps still have to close, is a ``Gap`` naming the decision; the
build step that closes it removes the entry, and the test then insists on the route.
"""

import importlib
import pkgutil
from dataclasses import dataclass

from fastapi.routing import APIRoute

import app.admin

CREATE_PREFIXES = ("create_", "add_", "register_")
CREATE_SUFFIXES = ("_create",)
"""A POST handler named like this creates an object; the test demands its counterparts."""


@dataclass(frozen=True)
class Gap:
    """A missing route that a decision allows, or a build step still has to add."""

    decision: str


@dataclass(frozen=True)
class Creatable:
    """One creatable object: the routes that change and delete what the create route made."""

    change: str | Gap
    delete: str | Gap


PERMANENT_HOLIDAY_DATE = Gap("ADR 0084 context: a holiday date is its identity, remove and add")
PERMANENT_HOLIDAY_RANGE = Gap("ADR 0084 context: the Dutch holidays are dates, each removed alone")

CREATABLES: dict[str, Creatable] = {
    # ADR 0084 step 4: an empty organisation is deleted from its edit form.
    "/admin/organisations": Creatable(
        change="/admin/organisations/{org}",
        delete="/admin/organisations/{org}/delete",
    ),
    # ADR 0084 step 2: a group is archived, from its settings.
    "/admin/groups": Creatable(
        change="/admin/groups/{slug}/settings",
        delete="/admin/groups/{slug}/settings/archive",
    ),
    "/admin/groups/{slug}/staffing": Creatable(
        change="/admin/groups/{slug}/staffing/{rule_id}",
        delete="/admin/groups/{slug}/staffing/{rule_id}/delete",
    ),
    "/admin/groups/{slug}/exceptions": Creatable(
        change=Gap("ADR 0084 step 5: change a staffing exception in place"),
        delete="/admin/groups/{slug}/exceptions/{override_id}/delete",
    ),
    # ADR 0055: answers refer to a field, so it is retired rather than deleted.
    "/admin/groups/{slug}/settings/form": Creatable(
        change="/admin/groups/{slug}/settings/form/{key}",
        delete="/admin/groups/{slug}/settings/form/{key}/retire",
    ),
    "/admin/groups/{slug}/reminders": Creatable(
        change=Gap("ADR 0084 step 5: change a reminder rule in place"),
        delete="/admin/groups/{slug}/reminders/{channel}/{minutes}/delete",
    ),
    "/admin/holidays": Creatable(
        change=Gap("ADR 0084 step 5: rename a holiday calendar"),
        delete="/admin/holidays/{calendar}/delete",
    ),
    "/admin/holidays/{calendar}/days": Creatable(
        change=PERMANENT_HOLIDAY_DATE,
        delete="/admin/holidays/{calendar}/days/{holiday_id}/delete",
    ),
    "/admin/holidays/{calendar}/national": Creatable(
        change=PERMANENT_HOLIDAY_RANGE,
        delete=PERMANENT_HOLIDAY_RANGE,
    ),
    "/admin/roles": Creatable(
        change="/admin/roles/{role_id}",
        delete="/admin/roles/{role_id}/delete",
    ),
    "/admin/api/clients": Creatable(
        change="/admin/api/clients/{client_id}",
        delete=Gap("ADR 0084 step 6: delete a disabled API client"),
    ),
    # ADR 0083 step 2: an organisation's identity provider, changed and removed on its page.
    "/admin/organisations/{org}/login": Creatable(
        change="/admin/organisations/{org}/login/{idp_key}",
        delete="/admin/organisations/{org}/login/{idp_key}/delete",
    ),
    "/admin/organisations/{org}/closure/scenarios": Creatable(
        change="/admin/organisations/{org}/closure/scenarios/{scenario_id}",
        delete="/admin/organisations/{org}/closure/scenarios/{scenario_id}/delete",
    ),
    "/admin/groups/{slug}/closure/scenarios": Creatable(
        change="/admin/groups/{slug}/closure/scenarios/{scenario_id}",
        delete="/admin/groups/{slug}/closure/scenarios/{scenario_id}/delete",
    ),
}
"""Every create route of the portal, by hand (ADR 0084 context table), with its counterparts."""


def admin_post_routes() -> dict[str, str]:
    """Return every POST route of the admin routers as path → handler name."""
    found: dict[str, str] = {}
    for module in pkgutil.iter_modules(app.admin.__path__):
        router = getattr(importlib.import_module(f"app.admin.{module.name}"), "router", None)
        for route in getattr(router, "routes", []):
            if isinstance(route, APIRoute) and "POST" in route.methods:
                found[route.path] = route.endpoint.__name__
    return found


def is_create(handler: str) -> bool:
    """Return whether a handler name says it creates an object."""
    return handler.startswith(CREATE_PREFIXES) or handler.endswith(CREATE_SUFFIXES)


def test_every_create_route_is_registered_with_its_counterparts() -> None:
    creates = {path for path, handler in admin_post_routes().items() if is_create(handler)}

    assert creates == set(CREATABLES), (
        "a create route is missing from CREATABLES (register its change and delete routes, "
        "or a Gap naming the decision), or a registered one is gone"
    )


def test_every_registered_change_and_delete_route_exists() -> None:
    posts = admin_post_routes()
    missing = {
        f"{path} {kind}": counterpart
        for path, creatable in CREATABLES.items()
        for kind, counterpart in (("change", creatable.change), ("delete", creatable.delete))
        if isinstance(counterpart, str) and counterpart not in posts
    }

    assert missing == {}


def test_a_gap_names_the_decision_that_allows_it() -> None:
    gaps = [
        counterpart
        for creatable in CREATABLES.values()
        for counterpart in (creatable.change, creatable.delete)
        if isinstance(counterpart, Gap)
    ]

    assert all(gap.decision.startswith("ADR ") for gap in gaps)
    # ADR 0084 steps 5 and 6 close four of these; three are the decision's own exceptions.
    assert len(gaps) == 7


def test_the_heuristic_finds_the_known_creators() -> None:
    assert is_create("create_group")
    assert is_create("add_reminder")
    assert is_create("organisation_create")
    assert not is_create("save_settings")
    assert not is_create("remove_hours")
