"""Personal data never appears on overview, list, export or alert pages (ADR 0063 §2, §3).

Bookings whose personal-data fields hold unique marker values are stored in the demo group,
and every admin GET route is fetched as the superuser, who sees everything. A marker in a
response is a leak, unless ADR 0063 §4 allows it there: the booking detail page shows the
answers, and the participant's name, address and number only after *Show contact details*
(ADR 0044 §2, the product owner's choice of 2026-11-08), the queue item the first name and the
join link, and the group's own agenda, queue and planner pages the first name only
(``FIRST_NAME_PAGES``).

The templates are discovered from the file system: every admin template must have been
rendered during the sweep, so a template added later is covered without editing this test.
A route with path parameters needs a concrete path in ``PATHS``; a new one fails the sweep
until it is added. The same walk checks that opening an admin page writes nothing to the
audit log, except the audited downloads of ``tests/support/downloads.py``, which refuse a
request another site makes (ASVS V3.5.3). The whole file runs in the fast lane (no ``matrix``
marker). The marker
bookings are the fixture ``marked`` (``tests/admin/conftest.py``), stored by
``tests/support/privacy_sweep.py``.
"""

import json
from collections import Counter
from dataclasses import fields
from pathlib import Path

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.admin.overview_charts import CHART_SERIES, KIND_OF_VIEW
from app.admin.routes import PUBLIC_ADMIN_PATHS
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, AuditFilter, Outcome, Source
from app.domain.privacy import ALIASES, PII_FIELDS
from app.domain.staff import StaffFilter
from app.domain.storage import FREE_SPACE_CRITICAL, STORAGE_EVENT
from app.web.widgets import OVERVIEW_PATH, WIDGETS
from tests.admin.conftest import SAME_ORIGIN, Site
from tests.support.admin_site import AuditRow, audit_events, post_form, site_app, site_uow
from tests.support.downloads import AUDITED_DOWNLOADS
from tests.support.privacy_sweep import MARKERS, QUEUE_ITEM, Fixture, marker_leaks
from tests.support.routes import all_routes

ADMIN_TEMPLATES = Path("app/admin/templates")
TEMPLATE_NAMES = frozenset(
    str(p.relative_to(ADMIN_TEMPLATES)) for p in ADMIN_TEMPLATES.rglob("*.html")
)
"""Every admin template, found on disk; each must be rendered by the sweep."""

DETAIL = "/admin/groups/demo/bookings/{id}"
DETAIL_CONTACT = DETAIL + "?contact=show"
"""The booking page after *Show contact details*: the one page with the name, address and number."""
CONTACT_MARKERS = frozenset(
    MARKERS[f] for f in ("first_name", "last_name", "email", "phone", "form_answers")
)
"""What the booking page shows on request; without the request, the answers only."""
QUEUE_ITEM_LIVE = QUEUE_ITEM + "/live"
"""The claimed booking's status fragment (ADR 0072): the join link travels with it."""
FIRST_NAME_PAGES = frozenset(
    {
        "/admin/groups/demo/queue/live",
        "/admin/groups/demo/agenda",
        "/admin/groups/demo/agenda/live",
        "/admin/groups/demo/agenda/week",
        "/admin/groups/demo/agenda/waiting",
        "/admin/groups/demo/agenda/slot-rebook?start=2026-01-09T08:00:00Z",
        "/admin/groups/demo/agenda/bookings/{id}/move",
        "/admin/groups/demo/agenda/bookings/{id}/cancel",
        "/admin/groups/demo/agenda/bookings/{id}/rebook",
        "/admin/groups/demo/queue",
    }
)
"""The group's own booking pages for ``bookings.view`` and ``queue.work``: the first name
only, the same minimum that goes to the contact centre (ADR 0043, 0044; ADR 0063 §4)."""

PATHS: dict[str, str] = {
    "/admin/groups/{slug}": "/admin/groups/demo",
    "/admin/groups/{slug}/settings": "/admin/groups/demo/settings",
    "/admin/groups/{slug}/settings/form": "/admin/groups/demo/settings/form",
    "/admin/groups/{slug}/settings/form/{key}": "/admin/groups/demo/settings/form/onderwerp",
    "/admin/groups/{slug}/settings/routing": "/admin/groups/demo/settings/routing",
    "/admin/groups/{slug}/settings/routing/subjects/{code}": (
        "/admin/groups/demo/settings/routing/subjects/onderwerp"
    ),
    "/admin/groups/{slug}/settings/archive": "/admin/groups/demo/settings/archive",
    "/admin/groups/{slug}/staffing": "/admin/groups/demo/staffing",
    "/admin/groups/{slug}/staffing/{rule_id}": "/admin/groups/demo/staffing/{rule}",
    "/admin/groups/{slug}/staffing/{rule_id}/split": "/admin/groups/demo/staffing/{rule}/split",
    "/admin/groups/{slug}/hours": "/admin/groups/demo/hours",
    "/admin/groups/{slug}/hours/{rule_id}": "/admin/groups/demo/hours/{rule}",
    "/admin/groups/{slug}/exceptions": "/admin/groups/demo/exceptions",
    "/admin/groups/{slug}/reminders": "/admin/groups/demo/reminders",
    "/admin/groups/{slug}/exceptions/{override_id}": "/admin/groups/demo/exceptions/{override}",
    "/admin/groups/{slug}/reminders/{channel}/{minutes}": "/admin/groups/demo/reminders/email/180",
    "/admin/groups/{slug}/messages": "/admin/groups/demo/messages",
    "/admin/groups/{slug}/messages/{text_locale}": "/admin/groups/demo/messages/nl",
    "/admin/groups/{slug}/holidays": "/admin/groups/demo/holidays",
    "/admin/groups/{slug}/agenda": "/admin/groups/demo/agenda",
    "/admin/groups/{slug}/agenda/live": "/admin/groups/demo/agenda/live",
    "/admin/groups/{slug}/agenda/week": "/admin/groups/demo/agenda/week",
    "/admin/groups/{slug}/agenda/waiting": "/admin/groups/demo/agenda/waiting",
    "/admin/groups/{slug}/agenda/slot-rebook": (
        "/admin/groups/demo/agenda/slot-rebook?start=2026-01-09T08:00:00Z"
    ),
    "/admin/groups/{slug}/agenda/bookings/{booking_id}/move": (
        "/admin/groups/demo/agenda/bookings/{id}/move"
    ),
    "/admin/groups/{slug}/agenda/bookings/{booking_id}/cancel": (
        "/admin/groups/demo/agenda/bookings/{id}/cancel"
    ),
    "/admin/groups/{slug}/agenda/bookings/{booking_id}/rebook": (
        "/admin/groups/demo/agenda/bookings/{id}/rebook"
    ),
    "/admin/groups/{slug}/bookings/{booking_id}": DETAIL,
    "/admin/groups/{slug}/queue": "/admin/groups/demo/queue",
    "/admin/groups/{slug}/queue/{booking_id}": QUEUE_ITEM,
    "/admin/groups/{slug}/queue/live": "/admin/groups/demo/queue/live",
    "/admin/groups/{slug}/queue/{booking_id}/live": QUEUE_ITEM_LIVE,
    "/admin/groups/{slug}/closure": "/admin/groups/demo/closure",
    "/admin/groups/{slug}/contact": "/admin/groups/demo/contact",
    "/admin/groups/{slug}/usage": "/admin/groups/demo/usage",
    "/admin/groups/{slug}/closure/contacts": "/admin/groups/demo/closure/contacts",
    "/admin/groups/{slug}/closure/scenarios": "/admin/groups/demo/closure/scenarios",
    "/admin/groups/{slug}/closure/scenarios/{scenario_id}": (
        "/admin/groups/demo/closure/scenarios/{scenario}"
    ),
    "/admin/groups/{slug}/closure/scenarios/{scenario_id}/contacts": (
        "/admin/groups/demo/closure/scenarios/{scenario}/contacts"
    ),
    "/admin/groups/{slug}/closure/scenarios/{scenario_id}/image": (
        "/admin/groups/demo/closure/scenarios/{scenario}/image"
    ),
    "/admin/groups/{slug}/closure/bookings/{booking_id}/move": (
        "/admin/groups/demo/closure/bookings/{on_hold}/move"
    ),
    "/admin/organisations/{org}": "/admin/organisations/demo",
    "/admin/organisations/{org}/edit": "/admin/organisations/demo/edit",
    "/admin/organisations/{org}/delete": "/admin/organisations/demo/delete",
    "/admin/organisations/{org}/settings": "/admin/organisations/demo/settings",
    "/admin/organisations/{org}/configuration": "/admin/organisations/demo/configuration",
    "/admin/organisations/{org}/texts/{text_locale}": "/admin/organisations/demo/texts/nl",
    "/admin/organisations/{org}/texts": "/admin/organisations/demo/texts",
    "/admin/organisations/{org}/branding": "/admin/organisations/demo/branding",
    "/admin/organisations/{org}/branding/theme.css": "/admin/organisations/demo/branding/theme.css",
    "/admin/organisations/{org}/channels": "/admin/organisations/demo/channels",
    "/admin/organisations/{org}/holidays": "/admin/organisations/demo/holidays",
    "/admin/organisations/{org}/closure": "/admin/organisations/demo/closure",
    "/admin/organisations/{org}/contacts": "/admin/organisations/demo/contacts",
    "/admin/organisations/{org}/usage": "/admin/organisations/demo/usage",
    "/admin/organisations/{org}/embedding": "/admin/organisations/demo/embedding",
    "/admin/organisations/{org}/closure/scenarios": "/admin/organisations/demo/closure/scenarios",
    "/admin/organisations/{org}/closure/scenarios/{scenario_id}": (
        "/admin/organisations/demo/closure/scenarios/{org_scenario}"
    ),
    "/admin/organisations/{org}/closure/scenarios/{scenario_id}/contacts": (
        "/admin/organisations/demo/closure/scenarios/{org_scenario}/contacts"
    ),
    "/admin/organisations/{org}/closure/scenarios/{scenario_id}/image": (
        "/admin/organisations/demo/closure/scenarios/{org_scenario}/image"
    ),
    "/admin/roles/{role_id}": "/admin/roles/{role}",
    "/admin/staff/{member_id}": "/admin/staff/{member}",
    "/admin/help/{topic}": "/admin/help/advanced-settings",
    "/admin/holidays/{calendar}": "/admin/holidays/nationaal",
    "/admin/api/clients/{client_id}": "/admin/api/clients/{client}",
    # ADR 0083 step 2: the organisation's identity providers.
    "/admin/organisations/{org}/login": "/admin/organisations/demo/login",
    "/admin/organisations/{org}/login/new": "/admin/organisations/demo/login/new",
    "/admin/organisations/{org}/login/{idp_key}": "/admin/organisations/demo/login/org-a",
    # ADR 0085 step 4: the log destinations.
    "/admin/logging/{destination_id}": "/admin/logging/1",
}
"""Concrete path per parametrised admin GET route; the placeholders in braces are filled
from ``Fixture``."""


def _get_routes(site: Site) -> list[APIRoute]:
    """Every admin GET route that an admin can open."""
    app = site_app(site)
    return [
        route
        for route in all_routes(app.routes)  # type: ignore[attr-defined]
        if "GET" in route.methods
        and route.path.startswith("/admin")
        and route.path not in PUBLIC_ADMIN_PATHS
    ]


def _concrete(route: APIRoute, found: Fixture) -> str:
    """Return the path to fetch for ``route``; a parametrised route must be in ``PATHS``."""
    if "{" not in route.path:
        return route.path
    assert route.path in PATHS, f"add a concrete path for {route.path} to PATHS"
    return found.fill(PATHS[route.path])


def _allowed(path: str, found: Fixture) -> set[str]:
    """The markers ADR 0063 §4 allows on ``path``."""
    if path == found.fill(DETAIL_CONTACT):
        return set(CONTACT_MARKERS)
    if path == found.fill(DETAIL):
        return {MARKERS["form_answers"]}
    if path in {found.fill(QUEUE_ITEM), found.fill(QUEUE_ITEM_LIVE)}:
        return {MARKERS["first_name"], MARKERS["guest_join_url"]}
    if path in {found.fill(p) for p in FIRST_NAME_PAGES}:
        return {MARKERS["first_name"]}
    return set()


def _written(before: Counter[AuditRow], after: list[AuditRow]) -> list[str]:
    """Return the actions one request added to the audit log, refusals left out.

    A refusal (``denied``) records what the browser asked for, not something the admin did.
    """
    added = Counter(after) - before
    return sorted(action for _, action, outcome, _ in added.elements() if outcome != Outcome.DENIED)


async def test_no_marker_leaves_the_allowed_pages_and_every_template_is_swept(
    marked: tuple[Site, Fixture],
) -> None:
    site, found = marked

    # The pages that may show personal data first: they prove the markers are live, and the
    # audit log then holds a view of the marker booking before the log and its export are swept.
    detail = (await site.client.get(found.fill(DETAIL))).text
    assert MARKERS["form_answers"] in detail, "the detail page does not show the answer"
    page = found.fill(DETAIL)
    button = {**SAME_ORIGIN, "referer": f"http://testserver{page}"}  # the page's own button
    shown = await site.client.get(found.fill(DETAIL_CONTACT), headers=button)
    assert MARKERS["phone"] in shown.text, "the detail page does not show the contact on request"
    assert not marker_leaks(shown.text, _allowed(found.fill(DETAIL_CONTACT), found))
    queue_item = (await site.client.get(found.fill(QUEUE_ITEM))).text
    assert MARKERS["first_name"] in queue_item
    assert MARKERS["guest_join_url"] in queue_item
    agenda = (await site.client.get("/admin/groups/demo/agenda")).text
    assert MARKERS["first_name"] in agenda, "the agenda does not show today's booking"

    leaks: dict[str, set[str]] = {}
    statuses: dict[str, int] = {}
    audited: dict[str, list[str]] = {}
    for route in sorted(_get_routes(site), key=lambda r: r.path):
        path = _concrete(route, found)
        before = Counter(await audit_events(site))
        response = await site.client.get(path)
        statuses[path] = response.status_code
        if found_leaks := marker_leaks(response.text, _allowed(path, found)):
            leaks[path] = found_leaks
        if written := _written(before, await audit_events(site)):
            audited[path] = written
    assert leaks == {}, "personal data on a list, overview, export or alert page (ADR 0063 §2)"
    # ASVS V3.5.3: opening an admin page writes nothing to the audit log; the downloads that do
    # refuse a request another site makes (``tests/admin/test_audited_downloads.py``).
    expected = {path: [action] for path, action in AUDITED_DOWNLOADS.items()}
    assert audited == expected, "a GET writes an audit event: guard it and add it to the list"
    # An image route has none to show; the certificate request download (ADR 0072) has no
    # request waiting, and the internal root (ADR 0112) no file without the internal authority.
    # All answer 404 on purpose and carry no personal data.
    no_content = ("/image", "/tls/csr.pem", "/tls/ca.crt")
    not_reached = {p: s for p, s in statuses.items() if s >= 400 and not p.endswith(no_content)}
    assert not_reached == {}, "a swept page did not render; fix its path or the fixture"

    # ADR 0072: the claimed booking's fragment once the claim is gone carries no booking data.
    async with site_uow(site).locked() as tx:
        await tx.release_claim(found.queued_id)
    lost = await site.client.get(found.fill(QUEUE_ITEM_LIVE))
    assert lost.status_code == 200
    assert not marker_leaks(lost.text, set()), "claim lost fragment"

    # The chart views of the overview widgets (ADR 0061 §5) are other templates on the same
    # routes, reached only by asking for them: counts and group names, never a marker.
    for item in WIDGETS:
        if not (item.available and item.key in CHART_SERIES):
            continue
        for chart_view in (v for v in item.views if v in KIND_OF_VIEW):
            for path in (
                f"{item.path}?view={chart_view.value}",
                f"{OVERVIEW_PATH}?{item.key}={chart_view.value}",
            ):
                response = await site.client.get(path)
                assert response.status_code == 200, path
                assert "<svg" in response.text or "widget__empty" in response.text, path
                assert not marker_leaks(response.text, set()), path

    # ADR 0117 point 11: the report families are the same routes with another `view`; their
    # templates carry counts and codes, never a marker.
    for scope in ("/admin/groups/demo/usage", "/admin/organisations/demo/usage"):
        for view in ("queue", "capacity", "channels"):
            response = await site.client.get(f"{scope}?view={view}")
            assert response.status_code == 200, (scope, view)
            assert not marker_leaks(response.text, set()), (scope, view)

    # ADR 0099: the plan of a configuration document, with a change in it, shows keys and field
    # names of the document and never a marker.
    exported = await post_form(site, "/admin/settings/configuration/export")
    changed = json.loads(exported.content)
    changed["organisations"][0]["name"] = "Renamed"
    planned = await site.client.post(
        "/admin/settings/configuration/plan",
        files={"document": ("config.json", json.dumps(changed).encode(), "application/json")},
        headers=SAME_ORIGIN,
    )
    assert planned.status_code == 200, planned.text
    assert "Renamed" not in planned.text, "a plan shows field names, never values"
    assert not marker_leaks(planned.text, set()), "configuration plan"

    # ADR 0095: the invitation form, and the page of a refused action on a person.
    for path, response in (
        ("/admin/staff/invite", await site.client.get("/admin/staff/invite")),
        (
            "POST /admin/staff/directory/reset",
            await post_form(site, "/admin/staff/directory/reset", {"subject": "nobody"}),
        ),
    ):
        assert not marker_leaks(response.text, set()), path

    # ADR 0081: with a subject the routing pages, its edit page and the block checkboxes render.
    await post_form(
        site,
        "/admin/groups/demo/settings/routing/subjects",
        {"code": "onderwerp", "label_nl": "Onderwerp", "label_en": "Subject"},
    )
    for path in (
        "/admin/groups/demo/settings/routing",
        "/admin/groups/demo/settings/routing/subjects/onderwerp",
        "/admin/groups/demo/staffing",
        "/admin/groups/demo/exceptions",
    ):
        response = await site.client.get(path)
        assert response.status_code == 200, path
        assert not marker_leaks(response.text, set()), path

    # ADR 0061 §7: the alert banner shows only with an open outage; codes and slugs, no marker.
    async with site_uow(site).locked() as tx:
        await tx.add_audit_event(
            AuditEvent(
                site.clock.now(),
                SYSTEM_ACTOR,
                STORAGE_EVENT,
                Outcome.FAILURE,
                FREE_SPACE_CRITICAL,
                source=Source.SYSTEM,
            )
        )
    banner = await site.client.get("/admin/")
    assert 'class="alert alert-banner"' in banner.text
    assert not marker_leaks(banner.text, set()), "alert banner"

    # The pages outside the happy path: a refusal, and the sign-out pages without a session.
    for path, response in (
        ("/admin/help/nope", await site.client.get("/admin/help/nope")),
        ("POST /admin/logout", await post_form(site, "/admin/logout", {})),
        ("/admin/logged-out", await site.client.get("/admin/logged-out")),
        # ADR 0083 point 13: the e-mail-first page, here with its refusal of an empty field.
        ("POST /admin/login", await post_form(site, "/admin/login", {"identifier": ""})),
        # ... and its answer for a valid one: the page that moves on to the provider.
        ("POST /admin/login ok", await post_form(site, "/admin/login", {"identifier": "super"})),
    ):
        assert not marker_leaks(response.text, set()), path

    never = sorted(TEMPLATE_NAMES - found.rendered)
    assert never == [], f"templates never rendered with the marker bookings: {never}"


def _query_names(dependant: Dependant) -> set[str]:
    """Every query parameter of a route, through its dependencies."""
    names = {p.name for p in dependant.query_params}
    for inner in dependant.dependencies:
        names |= _query_names(inner)
    return names


def test_no_list_filter_or_query_parameter_names_a_pii_field(site: Site) -> None:
    """ADR 0063 §3: a filter on a personal-data field would let a result say who someone is."""
    forbidden = PII_FIELDS | ALIASES | {"q", "search", "query"}
    for route in _get_routes(site):
        names = _query_names(route.dependant)
        assert not names & forbidden, route.path
    for narrow in (AuditFilter, StaffFilter):
        assert not {f.name for f in fields(narrow)} & forbidden, narrow.__name__


async def test_a_pii_value_in_any_query_parameter_finds_nothing_and_is_not_reflected(
    marked: tuple[Site, Fixture],
) -> None:
    """Abuse case "PII through list search or export" (docs/threat-model.md)."""
    site, found = marked
    for route in _get_routes(site):
        path = _concrete(route, found)
        if path in {found.fill(DETAIL), found.fill(QUEUE_ITEM)}:
            continue
        names = _query_names(route.dependant) | {"q", "search", "email", "name"}
        params = {name: MARKERS["email"] for name in names}
        response = await site.client.get(path, params=params)
        assert response.status_code < 500, path
        assert MARKERS["email"] not in response.text, path
        assert not marker_leaks(response.text, _allowed(path, found)), path
