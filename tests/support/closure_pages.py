"""The closure page of group demo in the admin portal (ADR 0024): its address and a closing form."""

CLOSURE = "/admin/groups/demo/closure"
"""The demo group's closure page."""
NOTICES = {
    "notice_nl": "Door een stroomstoring zijn wij gesloten.\nWe excuseren ons <b>hiervoor</b>.",
    "notice_en": "We are closed because of a power cut.",
}
"""The notice to participants in both languages, with markup that the page must escape."""
UNTIL_SATURDAY = {"ends_at": "2026-01-03", "ends_at_time": "00:00", **NOTICES}
"""The closing form: closed from now until Saturday 3 January 00:00, with ``NOTICES``."""
