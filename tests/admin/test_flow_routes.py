"""The flows of a service (ADR 0121 step 2d): list, editor, checklist and the lifecycle actions."""

import httpx
import pytest

from tests.admin.conftest import ORG_ADMIN, SAME_ORIGIN, Site
from tests.support.admin_site import audit_log
from tests.support.skill_seed import add_pool

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


async def test_the_flows_sub_tab_lists_the_existing_flow_read_only(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get(BASE)).text

    assert "Flows van deze dienst" in page
    assert "Een flow bepaalt hoe een verzoek wordt afgehandeld" in page
    assert "Bestaande werkwijze van deze dienst." in page
    assert "<code>appointment</code>" in page
    assert "Eigen bezetting (bestaand)" in page
    assert f'href="{BASE}/appointment"' not in page  # no edit link on a legacy flow
    assert f'href="{BASE}/new"' in page
    assert f'href="{BASE}"' in (await site.client.get("/admin/groups/demo/settings")).text


async def test_a_flow_is_created_edited_published_and_audited_with_codes_only(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    pool = await add_pool(site, "Advies")

    created = await create(site, capacity_pool_id=str(pool), default_appointment="yes")

    assert created.status_code == 303
    assert created.headers["location"] == f"{BASE}/advies?done=flow_created"
    editor = (await site.client.get(f"{BASE}/advies")).text
    assert 'value="Samen bespreken"' in editor
    assert "Concept opslaan" in editor
    assert "Deze flow is nog niet klaar voor gebruik" in editor
    assert (await save(site, label_nl="Nieuwe naam", capacity_pool_id=str(pool))).status_code == 303
    assert (await act(site, "publish")).status_code == 303
    after = (await site.client.get(f"{BASE}/advies")).text
    assert "Gepubliceerde revisie 1" in after
    assert "Nieuwe revisie voorbereiden" in after
    details = [e.detail for e in await audit_log(site) if e.action == "group.edit"]
    assert details == [
        "flow.created,flow:advies,revision:1",
        "flow.draft_saved,flow:advies,revision:1",
        "flow.published,flow:advies,revision:1",
    ]
    assert "Nieuwe naam" not in repr(await audit_log(site))


@pytest.mark.parametrize("kind", ["video", "telephone", "in_person", "none"])
async def test_the_read_outs_follow_the_saved_contact_channel(site: Site, kind: str) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site, interaction_kind="none")

    await save(site, interaction_kind=kind)
    page = (await site.client.get(f"{BASE}/advies")).text

    expected = {
        "video": ("zodra de deelnemer in de videoruimte is", "de videoruimte openen"),
        "telephone": ("direct, zonder aparte melding", "de deelnemer bellen"),
        "in_person": ("wanneer de deelnemer zich meldt", "het verzoek bekijken"),
        "none": ("direct, zonder aparte melding", "het verzoek bekijken"),
    }[kind]
    for text in expected:
        assert text in page
    assert "Gereed voor behandeling:" in page
    assert ("handling_action" in page) is (kind == "in_person")
    if kind == "in_person":
        assert "een werkplek toewijzen (volgt)" in page
        assert "disabled" in page.split("een werkplek toewijzen (volgt)")[0].rsplit("<option", 1)[1]
