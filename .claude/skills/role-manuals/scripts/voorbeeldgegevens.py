#!/usr/bin/env python3
"""Load sample data into the verify stack through real user paths, around the day of the run.

Fictional participants book on the coming working days of the demo group and confirm through
the link in their email; some move or cancel through their management link, and a few never
confirm. Everything goes through the group page, the emailed links and the portal: nothing is
written to the database and the clock is never shifted, so what a screenshot shows happened on
the day of the run. Today is left alone, so the queue of the capture story stays as it is.

More than ten bookings an hour needs a higher booking limit; a superuser sets it in the portal
(``RATE_LIMIT_BOOKINGS``, at most 30 an hour), the way an organisation would before a busy day.

Usage::

    python3 scripts/voorbeeldgegevens.py [--aantal 24]

Run it after ``stack.sh up`` and before ``capture.py``, outside an agent's macOS sandbox (it
starts Chrome). It reuses the helpers of ``capture.py`` and shares its state file, so the
evidence log names the sample data as a preparation step.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import unicodedata
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture as cap  # noqa: E402  (one way to drive the stack: the story's own helpers)

NAMES = [
    ("Lotte", "Bakker"),
    ("Mohammed", "Yilmaz"),
    ("Eva", "de Graaf"),
    ("Daan", "Visser"),
    ("Fatima", "Bouzid"),
    ("Ruben", "Smit"),
    ("Noor", "Hendriks"),
    ("Thomas", "van Dijk"),
    ("Sophie", "Mulder"),
    ("Yusuf", "Kaya"),
    ("Emma", "de Wit"),
    ("Lucas", "Bos"),
    ("Julia", "Vermeulen"),
    ("Milan", "Peters"),
    ("Sara", "Koster"),
    ("Bram", "Jacobs"),
    ("Lisa", "van Leeuwen"),
    ("Sem", "Dekker"),
    ("Anna", "Brouwer"),
    ("Jesse", "de Jong"),
    ("Zoë", "Willems"),
    ("Ömer", "Demir"),
    ("Fleur", "Kok"),
    ("Stijn", "Meijer"),
]
"""Made-up participants; their addresses are at voorbeeld.nl and never leave Mailpit."""

PREFERRED = ["09:30", "11:00", "14:00", "10:00", "13:30", "15:00", "09:00", "16:00"]
"""Times tried in this order on a day, so a few slots fill up to their staffing."""

RATE_LIMIT = "30"
"""Bookings per hour per address during the run: the ceiling the portal allows."""


def address(first: str, last: str) -> str:
    """Return the fictional email address of a sample participant."""
    plain = unicodedata.normalize("NFKD", f"{first}.{last}").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z.]", "", plain.lower()) + "@voorbeeld.nl"


def raise_rate_limit(run: cap.Run) -> str:
    """As the superuser, set the booking limit per address to the portal's ceiling."""
    page = run.staff("super")
    page.goto("/admin/settings/values#topic-rate_limits")
    field = page.get_by_label("Afspraken per IP-adres")
    before = field.input_value()
    wanted = re.sub(r"^\d+", RATE_LIMIT, before.strip()) if before.strip() else f"{RATE_LIMIT}/hour"
    if before != wanted:
        field.fill(wanted)
        cap.form_of(field).get_by_role("button", name="Opslaan").click()
        page.wait_for_load_state("load")
    page.goto("/admin/settings/values#topic-rate_limits")
    after = page.get_by_label("Afspraken per IP-adres").input_value()
    if not after.startswith(RATE_LIMIT):
        raise RuntimeError(f"the booking limit is still {after!r}")
    return f'stond op "{before}", gezet op "{after}"'


def free_times(run: cap.Run, day: str) -> list[str]:
    """Return the free times the group page offers on ``day``, as a participant sees them."""
    page = run.participant("controle", fresh=True)
    page.goto(f"/g/{cap.GROUP}")
    cap.wait_h1(page, cap.GROUP_NAME)
    cap.pick_day(page, day)
    page.locator("#times .time-button").first.wait_for(timeout=15000)
    return [t.strip() for t in page.locator("#times .time-button").all_inner_texts()]


def book_only(run: cap.Run, key: str, who: str, day: str, label: str) -> None:
    """Book without confirming: the booking waits for confirmation, as people sometimes leave it."""
    page = run.participant(who, fresh=True)
    page.goto(f"/g/{cap.GROUP}")
    cap.wait_h1(page, cap.GROUP_NAME)
    cap.pick_day(page, day)
    cap.time_button(page, label).click()
    cap.wait_h1(page, "Uw gegevens")
    cap.fill_contact(page, who)
    cap.submit_booking(page)
    run.bookings[key] = {
        "who": who,
        "day": day,
        "time": label,
        "status": "pending",
        "booked_at": cap.stamp(),
    }
    run.save()


def cancel(run: cap.Run, key: str) -> None:
    """Cancel through the management link in the confirmation email."""
    page = cap.open_manage_link(run, key)
    page.get_by_role("link", name="Afspraak annuleren").click()
    page.get_by_role("button", name="Ja, annuleren").click()
    page.get_by_role("status").filter(has_text="Uw afspraak is geannuleerd").first.wait_for(
        timeout=20000
    )
    run.bookings[key]["status"] = "cancelled"
    run.save()


def move(run: cap.Run, key: str, day: str) -> None:
    """Move through the management link to the first free time on ``day``."""
    page: Page = cap.open_manage_link(run, key)
    page.get_by_role("link", name="Afspraak verplaatsen").click()
    cap.wait_h1(page, "Afspraak verplaatsen")
    cap.pick_day(page, day)
    page.locator("#times .time-button").first.click()
    cap.wait_h1(page, "Nieuw moment bevestigen")
    page.get_by_role("button", name="Verplaatsen").click()
    page.get_by_role("status").filter(has_text="Uw afspraak is verplaatst").first.wait_for(
        timeout=20000
    )
    run.bookings[key].update({"status": "moved", "moved_to": day})
    run.save()


def main() -> int:
    """Load the sample data and record it as a preparation step of the story."""
    if ".claude/skills" in Path(__file__).resolve().as_posix():
        sys.exit(
            f"{Path(__file__).name}: copy the scripts to a workspace outside the repository first "
            "(see the role-manuals SKILL.md): manuals hold an organisation's own material."
        )
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--aantal", type=int, default=len(NAMES), help="sample participants (at most 24)"
    )
    args = parser.parse_args()
    stack = cap.load_stack()
    cap.assert_installation(stack, stack.installation)
    state = json.loads(cap.STATE_FILE.read_text()) if cap.STATE_FILE.exists() else {}
    state.setdefault("installation", stack.installation)
    counts = {"geboekt": 0, "bevestigd": 0, "onbevestigd": 0, "geannuleerd": 0, "verplaatst": 0}
    today = dt.date.fromisoformat(cap.today_iso())
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        run = cap.Run(
            stack=stack, mail=cap.Mail(stack.mail, stack.web), browser=browser, state=state
        )
        limit = raise_rate_limit(run)
        people = NAMES[: min(args.aantal, len(NAMES))]
        for index, (first, last) in enumerate(people):
            who, key = f"vb{index:02d}", f"vb-{index:02d}"
            cap.PEOPLE[who] = (first, last, address(first, last))
            day = cap.next_weekday(today, 1 + index // 3).isoformat()
            offered = free_times(run, day)
            label = next((t for t in PREFERRED if t in offered), offered[0] if offered else None)
            if label is None:
                print(f"  --   {key}: geen vrije tijd op {day}")
                continue
            if index % 8 == 7:
                book_only(run, key, who, day, label)
                counts["onbevestigd"] += 1
            else:
                cap.book_and_confirm(run, key, who, day, label)
                counts["bevestigd"] += 1
            counts["geboekt"] += 1
            print(f"  ok   {key}: {first} {last}, {day} {label}")
        for index in range(len(people)):
            key = f"vb-{index:02d}"
            booking = run.bookings.get(key, {})
            if booking.get("status") != "confirmed":
                continue
            if index % 6 == 2:
                cancel(run, key)
                counts["geannuleerd"] += 1
            elif index % 6 == 4:
                later = cap.next_weekday(dt.date.fromisoformat(booking["day"]), 2).isoformat()
                move(run, key, later)
                counts["verplaatst"] += 1
        summary = ", ".join(f"{n} {k}" for k, n in counts.items())
        run.note_prep(
            "voorbeeldgegevens",
            f"Voorbeeldgegevens geladen via de echte paden, op de dag van de opname: {summary}. "
            "Verzonnen deelnemers (@voorbeeld.nl), op de werkdagen na vandaag; "
            "vandaag bleef vrij. "
            "Boekingslimiet per adres als `super` via "
            "**Geavanceerde instellingen → Instellingen** "
            f"({limit}).",
        )
        browser.close()
    print(f"voorbeeldgegevens: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
