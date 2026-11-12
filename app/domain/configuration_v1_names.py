"""The names of the kinds of the version-1 format, in one place (ADR 0099).

A rule across objects reads the kinds of other slices by name. The names live here, so a rule
never spells one as a bare string and a renamed kind is a change in one file; the slices
declare their kinds with these names and export them under their own ``*_KIND`` aliases.
"""

PLATFORM_KIND = "platform"
PLATFORM_SETTING_KIND = "platform_setting"
PLATFORM_SECRET_KIND = "platform_secret"  # noqa: S105  # nosec B105 - a kind name, not a secret
API_SETTINGS_KIND = "api_settings"
LOG_DESTINATION_KIND = "log_destination"
ROLE_KIND = "role"
HOLIDAY_CALENDAR_KIND = "holiday_calendar"
HOLIDAY_KIND = "holiday"

ORGANISATION_KIND = "organisation"
ORGANISATION_SETTING_KIND = "organisation_setting"
ORGANISATION_TEXT_KIND = "organisation_text"
ORGANISATION_LOGO_KIND = "organisation_logo"
ORGANISATION_FONT_KIND = "organisation_font"

GROUP_KIND = "group"
GROUP_TEXT_KIND = "group_text"
API_CLIENT_KIND = "api_client"

PROVIDER_KIND = "identity_provider"
PROVIDER_KEY_KIND = "identity_provider_key"
PROVIDER_DOMAIN_KIND = "identity_provider_domain"
EMAIL_CHANNEL_KIND = "email_channel"
SMS_CHANNEL_KIND = "sms_channel"
ORGANISATION_SCENARIO_KIND = "organisation_scenario"
GROUP_SCENARIO_KIND = "group_scenario"
SCENARIO_IMAGE_KIND = "scenario_image"
PLATFORM_ALERT_TARGET_KIND = "platform_alert_target"
ORGANISATION_ALERT_TARGET_KIND = "organisation_alert_target"

SKILL_KIND = "skill"
CAPACITY_POOL_KIND = "capacity_pool"
LEGACY_POOL_KIND = "legacy_capacity_pool"
SERVICE_FLOW_KIND = "service_flow"
FLOW_REVISION_KIND = "flow_revision"
"""ADR 0121: the skills and counted pools of an organisation, and a group's legacy pool, flows
and their revisions."""
