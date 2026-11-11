"""Helpers that post the forms of the flow screens (ADR 0121 step 2d)."""

import httpx

from tests.admin.conftest import SAME_ORIGIN, Site

BASE = "/admin/groups/demo/flows"


def form_data(**changes: str) -> dict[str, str]:
    """Return the fields of a complete, valid editor form; ``changes`` override or add."""
    data = {
        "label_nl": "Samen bespreken",
        "label_en": "Discuss together",
        "intake_appointment": "yes",
        "interaction_kind": "none",
        "duration_minutes": "30",
        "dispatch": "own_queue",
        "completion": "operator",
        "collects_email": "yes",
    }
    return {**data, **changes}


async def create(site: Site, code: str = "advies", **changes: str) -> httpx.Response:
    """Post the form that creates a flow."""
    data = form_data(flow_code=code, **changes)
    return await site.client.post(BASE, data=data, headers=SAME_ORIGIN)


async def save(site: Site, code: str = "advies", **changes: str) -> httpx.Response:
    """Post the editor form of flow ``code``."""
    return await site.client.post(f"{BASE}/{code}", data=form_data(**changes), headers=SAME_ORIGIN)


async def act(site: Site, action: str, code: str = "advies") -> httpx.Response:
    """Post the lifecycle action ``action`` of flow ``code``."""
    return await site.client.post(f"{BASE}/{code}/{action}", headers=SAME_ORIGIN)
