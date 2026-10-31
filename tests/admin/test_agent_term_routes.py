"""The agent term in the portal (ADR 0077): what the organisation and the group call the person
who takes the call, where it is edited, and where it shows."""

from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SAME_ORIGIN, Site

ORG_TEXTS = "/admin/organisations/demo/texts/nl"
GROUP_TEXTS = "/admin/groups/demo/messages/nl"
STAFFING = "/admin/groups/demo/staffing"
BEHANDELAAR = {"term.agent": "behandelaar", "term.agents": "behandelaars"}
AGENT = {"term.agent": "agent", "term.agents": "agenten"}


async def test_the_organisation_editor_shows_the_shipped_words_in_their_own_card(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get(ORG_TEXTS)).text

    assert "Hoe u de medewerker noemt" in page
    assert 'name="term.agent"' in page
    assert 'value="medewerker"' in page
    assert 'value="medewerkers"' in page
    assert "Eén medewerker die het gesprek aanneemt" in page


async def test_the_organisation_term_shows_in_the_portal_of_its_groups(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    assert "Aantal medewerkers" in (await site.client.get(STAFFING)).text

    await site.login_as(ORG_ADMIN)
    saved = await site.client.post(ORG_TEXTS, data=BEHANDELAAR, headers=SAME_ORIGIN)

    assert saved.headers["location"] == f"{ORG_TEXTS}?done=texts_saved"
    await site.login_as(GROUP_ADMIN)
    page = (await site.client.get(STAFFING)).text
    assert "Aantal behandelaars" in page
    assert "Aantal medewerkers" not in page
    english = (await site.client.get(f"{STAFFING}?lang=en")).text
    assert "Number of agents" in english  # the other locale keeps its shipped word


async def test_the_audit_log_names_the_keys_but_not_the_words(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await site.client.post(ORG_TEXTS, data=BEHANDELAAR, headers=SAME_ORIGIN)

    page = (await site.client.get("/admin/audit")).text

    assert "term.agent" in page
    assert "behandelaar" not in page


async def test_one_word_alone_is_refused_and_the_form_keeps_what_was_typed(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    refused = await site.client.post(
        ORG_TEXTS, data={"term.agent": "behandelaar", "term.agents": ""}, headers=SAME_ORIGIN
    )

    assert refused.status_code == 422
    assert "Enkelvoud en meervoud wijzigt u samen" in refused.text
    assert 'value="behandelaar"' in refused.text
    await site.login_as(GROUP_ADMIN)
    assert "Aantal medewerkers" in (await site.client.get(STAFFING)).text  # nothing stored


async def test_a_word_with_braces_or_one_letter_is_refused(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    for word in ("{agent}", "x"):
        refused = await site.client.post(
            ORG_TEXTS, data={"term.agent": word, "term.agents": word}, headers=SAME_ORIGIN
        )
        assert refused.status_code == 422, word


async def test_the_group_term_beats_the_organisation_term(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await site.client.post(ORG_TEXTS, data=BEHANDELAAR, headers=SAME_ORIGIN)

    await site.login_as(GROUP_ADMIN)
    editor = (await site.client.get(GROUP_TEXTS)).text
    assert 'value="behandelaar"' in editor  # the default of the group is the organisation's word
    saved = await site.client.post(GROUP_TEXTS, data=AGENT, headers=SAME_ORIGIN)

    assert saved.headers["location"] == f"{GROUP_TEXTS}?done=texts_saved"
    page = (await site.client.get(STAFFING)).text
    assert "Aantal agenten" in page
    assert "Aantal behandelaars" not in page


async def test_a_group_that_brings_its_default_back_follows_the_organisation_again(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await site.client.post(ORG_TEXTS, data=BEHANDELAAR, headers=SAME_ORIGIN)
    await site.login_as(GROUP_ADMIN)
    await site.client.post(GROUP_TEXTS, data=AGENT, headers=SAME_ORIGIN)

    await site.client.post(GROUP_TEXTS, data=BEHANDELAAR, headers=SAME_ORIGIN)

    page = (await site.client.get(STAFFING)).text
    assert "Aantal behandelaars" in page


async def test_the_organisation_term_does_not_reach_another_organisation(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await site.client.post(ORG_TEXTS, data=BEHANDELAAR, headers=SAME_ORIGIN)

    await site.login_as(OUTSIDER)

    refused = await site.client.post(ORG_TEXTS, data=AGENT, headers=SAME_ORIGIN)
    assert refused.status_code in (403, 404)
