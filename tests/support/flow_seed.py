"""Seed flows for the tests of ADR 0121 step 2d, through the application's own flow service.

The route tests of the flows stand on what these helpers store, not on forms posted and codes
read back from rendered pages. ``draft`` is a complete, non-video draft input; ``add_flow`` stores
one as a draft flow through ``FlowService`` (every rule and audit event applies).
"""

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from app.domain.authorization import Principal
from app.domain.flow_service import FlowDraftInput
from app.domain.routing import Subject
from app.domain.service_flows import (
    CompletionPolicy,
    DispatchKind,
    HandlingAction,
    IntakeMode,
    InteractionKind,
)
from tests.admin.conftest import SUPER, Site
from tests.support.admin_site import site_app

LABELS = {"nl": "Balie 7", "en": "Desk 7"}


def draft(pool_id: int | None = None, **changes: Any) -> FlowDraftInput:
    """Return a complete draft of a flow without conversation on the own queue; ``changes`` win."""
    base = FlowDraftInput(
        LABELS,
        frozenset({IntakeMode.APPOINTMENT}),
        InteractionKind.NONE,
        30,
        pool_id,
        frozenset(),
        frozenset(),
        DispatchKind.OWN_QUEUE,
        HandlingAction.VIEW_REQUEST,
        CompletionPolicy.OPERATOR,
        True,
        frozenset(),
    )
    return replace(base, **changes)


async def add_flow(
    site: Site,
    code: str = "advies",
    pool_id: int | None = None,
    *,
    group: str = "demo",
    by: Principal = SUPER,
    **changes: Any,
) -> None:
    """Create the draft flow ``code`` in service ``group`` through the flow service."""
    await site_app(site).state.flow_service.create(by, group, code, draft(pool_id, **changes))


async def publish_flow(
    site: Site, code: str = "advies", *, group: str = "demo", by: Principal = SUPER
) -> None:
    """Publish the draft of flow ``code`` (the administrator has logged in, so has a record)."""
    await site_app(site).state.flow_service.publish(by, group, code)


async def add_subjects(
    site: Site, codes: Mapping[str, str], *, group: str = "demo", by: Principal = SUPER
) -> None:
    """Give the service the subjects ``codes`` (code to Dutch label; English is the same)."""
    for code, label in codes.items():
        subject = Subject.create(code, {"nl": label, "en": label})
        await site_app(site).state.routing_service.add_subject(by, group, subject)
