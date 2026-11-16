"""The settings form of the demo group in the admin portal, for the HTTP tests."""

import httpx

from tests.support.admin_site import Site, post_form

SETTINGS = "/admin/groups/demo/settings"
"""The demo group's settings page, where the form is posted."""

SETTINGS_FORM = {
    "name": "Demo-team",
    "timezone": "Europe/Amsterdam",
    "slot_minutes": "30",
    "min_notice_minutes": "60",
    "horizon_days": "30",
    "dispatch_lead_minutes": "5",
    "dispatch_kind": "contact_center",
    "participant_change_lead_minutes": "60",
    "trial_call_enabled": "yes",
    "join_client": "browser",
    "trial_call_minutes": "30",
    "trial_calls_per_booking": "3",
    "planner_change_min_lead_minutes": "60",
    "queue_visible_from_minutes": "15",
    "max_bookings_per_email": "3",
    "reschedule_auto_send": "no",
    "reschedule_auto_delay_minutes": "15",
    "reschedule_auto_max_bookings": "10",
    "queue_free_pick_allowed": "no",
    "phone_mode": "required",
    "video_host_email": "host-demo@example.nl",
    "wxcc_business_address": "openplanner@example.biz",
    "wxcc_channel": "openplanner-video",
    "wxcc_routing_value": "team-42",
    "not_picked_up_minutes": "10",
    "embedding_enabled": "yes",
    "dispatch_fallback": "none",
    "fallback_space_room_id": "",
}
"""A complete, valid settings form for the demo group."""


async def save_settings(site: Site, data: dict[str, str]) -> httpx.Response:
    """Post ``data`` as the demo group's settings form."""
    return await post_form(site, SETTINGS, data)
