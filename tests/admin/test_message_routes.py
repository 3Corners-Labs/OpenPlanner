"""Reminder and message-text screens (ADR 0022): rules per group, texts per locale."""

from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, SAME_ORIGIN, Site

REMINDERS = "/admin/groups/demo/reminders"
TEXTS = "/admin/groups/demo/messages"
INTRO_DEFAULT = "Dit is een herinnering aan uw videogesprek met {group_name}."


async def test_org_admin_adds_and_removes_an_sms_reminder(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    added = await site.client.post(
        REMINDERS, data={"channel": "sms", "amount": "2", "unit": "hours"}, headers=SAME_ORIGIN
    )

    assert added.status_code == 303
    page = (await site.client.get(added.headers["location"])).text
    assert "2 uur vooraf" in page
    assert "Toegevoegd." in page
    # The organisation has no SMS service yet: the page says so instead of failing silently.
    assert "kanaal niet ingesteld" in page
    assert 'aria-current="page"' in page

    duplicate = await site.client.post(
        REMINDERS, data={"channel": "sms", "amount": "120", "unit": "minutes"}, headers=SAME_ORIGIN
    )
    assert duplicate.status_code == 422
    assert "bestaat al" in duplicate.text

    removed = await site.client.post(f"{REMINDERS}/sms/120/delete", headers=SAME_ORIGIN)
    assert removed.headers["location"] == f"{REMINDERS}?done=removed"
    assert "2 uur vooraf" not in (await site.client.get(REMINDERS)).text


async def test_an_offset_outside_the_range_is_linked_to_its_field(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await site.client.post(
        REMINDERS, data={"channel": "email", "amount": "1", "unit": "minutes"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 422
    assert "tussen 5 minuten en 30 dagen" in response.text


async def test_group_admin_has_no_reminder_tab_but_a_texts_tab(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    page = (await site.client.get("/admin/groups/demo")).text

    assert f'href="{REMINDERS}"' not in page
    assert f'href="{TEXTS}"' in page


async def test_texts_show_the_default_and_store_only_changes(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    opened = await site.client.get(TEXTS)
    assert opened.headers["location"] == f"{TEXTS}/nl"
    page = (await site.client.get(f"{TEXTS}/nl")).text
    assert "Herinnering: videogesprek op {date} om {time}" in page  # the default in effect
    assert "U kunt gebruiken: {date}, {group_name}, {time}." in page

    saved = await site.client.post(
        f"{TEXTS}/nl",
        data={
            "message.reminder.subject": "Denkt u aan {date}?",
            "message.reminder.intro": INTRO_DEFAULT,
        },
        headers=SAME_ORIGIN,
    )
    assert saved.headers["location"] == f"{TEXTS}/nl?done=texts_saved"
    page = (await site.client.get(f"{TEXTS}/nl")).text
    assert 'value="Denkt u aan {date}?"' in page
    assert "Eigen tekst van deze groep." in page
    english = (await site.client.get(f"{TEXTS}/en")).text
    assert "Reminder: video call on {date} at {time}" in english  # other locale untouched


async def test_an_unknown_placeholder_is_refused_and_kept_in_the_form(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    response = await site.client.post(
        f"{TEXTS}/nl",
        data={"message.reminder.sms": "Hoi {first_name}, uw BSN is {bsn}"},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 422
    assert 'href="#message.reminder.sms"' in response.text
    assert "{manage_url}" in response.text  # the allowed placeholders are listed
    assert "uw BSN is {bsn}" in response.text


async def test_a_reminder_is_changed_from_its_own_page(site: Site) -> None:
    """ADR 0084 step 5: the channel and the offset change in place; a clash is refused."""
    await site.login_as(ORG_ADMIN)
    await site.client.post(
        REMINDERS, data={"channel": "sms", "amount": "2", "unit": "hours"}, headers=SAME_ORIGIN
    )
    page = (await site.client.get(REMINDERS)).text
    assert f'href="{REMINDERS}/sms/120"' in page

    form = (await site.client.get(f"{REMINDERS}/sms/120")).text
    assert '<option value="sms" selected>' in form
    assert 'value="2"' in form
    assert '<option value="hours" selected>' in form
    clash = await site.client.post(
        f"{REMINDERS}/sms/120",
        data={"channel": "email", "amount": "24", "unit": "hours"},  # the seed's own rule
        headers=SAME_ORIGIN,
    )
    changed = await site.client.post(
        f"{REMINDERS}/sms/120",
        data={"channel": "sms", "amount": "3", "unit": "hours"},
        headers=SAME_ORIGIN,
    )
    again = await site.client.post(
        f"{REMINDERS}/sms/180",
        data={"channel": "sms", "amount": "3", "unit": "hours"},
        headers=SAME_ORIGIN,
    )

    assert clash.status_code == 422
    assert "bestaat al" in clash.text
    assert changed.headers["location"] == f"{REMINDERS}?done=saved"
    assert again.headers["location"] == f"{REMINDERS}?done=unchanged"
    listing = (await site.client.get(REMINDERS)).text
    assert "3 uur vooraf" in listing
    assert "2 uur vooraf" not in listing


async def test_changing_an_unknown_reminder_is_not_found(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    assert (await site.client.get(f"{REMINDERS}/sms/999")).status_code == 404
    assert (await site.client.get(f"{REMINDERS}/telex/120")).status_code == 404
