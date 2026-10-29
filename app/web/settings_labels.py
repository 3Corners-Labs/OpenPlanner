"""Catalogue keys of the settings screens (ADR 0042), written out literally.

Keeping every key literal lets the catalogue test check that each one exists in Dutch and
English. A test checks ``SETTING_LABELS`` against the registry: one label per setting the
portal shows (class C and D), at the key the registry names.
"""

from app.domain.platform_settings import Source
from app.domain.settings_registry import SettingClass, Topic

SETTING_LABELS: dict[str, str] = {
    "log_level": "settings.field.log_level.label",
    "worker_poll_seconds": "settings.field.worker_poll_seconds.label",
    "maintenance_cache_seconds": "settings.field.maintenance_cache_seconds.label",
    "maintenance_retry_after_seconds": "settings.field.maintenance_retry_after_seconds.label",
    "maintenance_alert_hours": "settings.field.maintenance_alert_hours.label",
    "maintenance_alert_repeat_hours": "settings.field.maintenance_alert_repeat_hours.label",
    "staff_last_seen_interval_seconds": "settings.field.staff_last_seen_interval_seconds.label",
    "system_event_retention_days": "settings.field.system_event_retention_days.label",
    "audit_retention_days": "settings.field.audit_retention_days.label",
    "security_event_retention_days": "settings.field.security_event_retention_days.label",
    "diagnostic_event_retention_days": "settings.field.diagnostic_event_retention_days.label",
    "diagnostic_mode_hours": "settings.field.diagnostic_mode_hours.label",
    "security_event_window_minutes": "settings.field.security_event_window_minutes.label",
    "security_event_max_sources": "settings.field.security_event_max_sources.label",
    "audit_export_max_rows": "settings.field.audit_export_max_rows.label",
    "audit_export_max_days": "settings.field.audit_export_max_days.label",
    "embedding_max_origins": "settings.field.embedding_max_origins.label",
    "storage_low_free_percent": "settings.field.storage_low_free_percent.label",
    "storage_critical_free_percent": "settings.field.storage_critical_free_percent.label",
    "database_max_mb": "settings.field.database_max_mb.label",
    "rate_limit_bookings": "settings.field.rate_limit_bookings.label",
    "rate_limit_reads": "settings.field.rate_limit_reads.label",
    "rate_limit_agent_tokens": "settings.field.rate_limit_agent_tokens.label",
    "rate_limit_agent_calls": "settings.field.rate_limit_agent_calls.label",
    "admin_session_hours": "settings.field.admin_session_hours.label",
    "admin_reauth_minutes": "settings.field.admin_reauth_minutes.label",
    "oidc_role_claim": "settings.field.oidc_role_claim.label",
    "oidc_organisation_claim": "settings.field.oidc_organisation_claim.label",
    "oidc_group_claim": "settings.field.oidc_group_claim.label",
    "oidc_role_superuser": "settings.field.oidc_role_superuser.label",
    "oidc_role_org_admin": "settings.field.oidc_role_org_admin.label",
    "oidc_role_group_admin": "settings.field.oidc_role_group_admin.label",
    "oidc_role_auditor": "settings.field.oidc_role_auditor.label",
    "oidc_role_agent": "settings.field.oidc_role_agent.label",
    "smtp_host": "settings.field.smtp_host.label",
    "smtp_port": "settings.field.smtp_port.label",
    "smtp_starttls": "settings.field.smtp_starttls.label",
    "smtp_tls": "settings.field.smtp_tls.label",
    "smtp_username": "settings.field.smtp_username.label",
    "smtp_password": "settings.field.smtp_password.label",  # nosec B105 - an i18n key
    "mail_from": "settings.field.mail_from.label",
    "webex_client_id": "settings.field.webex_client_id.label",
    "webex_client_secret": "settings.field.webex_client_secret.label",  # nosec B105 - an i18n key
    "webex_refresh_token": "settings.field.webex_refresh_token.label",  # nosec B105 - an i18n key
    "webex_webhook_secret": "settings.field.webex_webhook_secret.label",  # nosec B105 - an i18n key
    "wxcc_api_base_url": "settings.field.wxcc_api_base_url.label",
    "wxcc_webhook_secret": "settings.field.wxcc_webhook_secret.label",  # nosec B105 - an i18n key
    "update_repository": "settings.field.update_repository.label",
    "update_check_token": "settings.field.update_check_token.label",  # nosec B105 - an i18n key
    "time_format": "settings.field.time_format.label",
    "admin_timezone": "settings.field.admin_timezone.label",
    "staff_dormant_after_days": "settings.field.staff_dormant_after_days.label",
    "shortfall_horizon_days": "settings.field.shortfall_horizon_days.label",
    "planner_changes_alert_per_hour": "settings.field.planner_changes_alert_per_hour.label",
    "planner_rebook_days": "settings.field.planner_rebook_days.label",
    "planner_rebook_reminder_days": "settings.field.planner_rebook_reminder_days.label",
}
"""The label of every class C and D setting, by key."""

TOPIC_LABELS: dict[Topic, str] = {
    Topic.BOOTSTRAP: "settings.topic.bootstrap",
    Topic.LOGIN: "settings.topic.login",
    Topic.HOST: "settings.topic.host",
    Topic.SWITCHES: "settings.topic.switches",
    Topic.DEVELOPMENT: "settings.topic.development",
    Topic.OPERATIONS: "settings.topic.operations",
    Topic.RETENTION: "settings.topic.retention",
    Topic.STORAGE: "settings.topic.storage",
    Topic.RATE_LIMITS: "settings.topic.rate_limits",
    Topic.ADMIN_SESSION: "settings.topic.admin_session",
    Topic.LOGIN_MAPPING: "settings.topic.login_mapping",
    Topic.MAIL: "settings.topic.mail",
    Topic.WEBEX: "settings.topic.webex",
    Topic.UPDATES: "settings.topic.updates",
    Topic.DISPLAY: "settings.topic.display",
    Topic.STAFF: "settings.topic.staff",
    Topic.PLANNER: "settings.topic.planner",
}
"""The heading of every topic."""

SOURCE_LABELS: dict[Source, str] = {
    Source.DEFAULT: "admin.settings.source.default",
    Source.PORTAL: "admin.settings.source.portal",
    Source.ORGANISATION: "admin.settings.source.organisation",
    Source.ENV: "admin.settings.source.env",
    Source.FILE: "admin.settings.source.file",
}
"""Where a value comes from."""

CLASS_LABELS: dict[SettingClass, str] = {
    SettingClass.BOOTSTRAP: "admin.settings.class.bootstrap",
    SettingClass.DEPLOYMENT: "admin.settings.class.deployment",
    SettingClass.PLATFORM: "admin.settings.class.platform",
    SettingClass.ORGANISATION: "admin.settings.class.organisation",
}
"""The name of every class."""

PROBLEM_LABELS: dict[str, str] = {
    "simulator_enabled": "admin.settings.problem.simulator_enabled",
    "management_token_key": "admin.settings.problem.management_token_key",  # nosec B105 - an i18n key
    "public_base_url": "admin.settings.problem.public_base_url",
    "session_secret": "admin.settings.problem.session_secret",  # nosec B105 - an i18n key
    "dev_placeholder": "admin.settings.problem.dev_placeholder",
    "webhook_base_url": "admin.settings.problem.webhook_base_url",
    "webex_incomplete": "admin.settings.problem.webex_incomplete",
    "oidc_incomplete": "admin.settings.problem.oidc_incomplete",
}
"""What production would refuse, by the code of ``Settings.production_problems``."""
