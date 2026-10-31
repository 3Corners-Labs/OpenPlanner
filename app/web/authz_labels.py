"""Catalogue keys for roles, permissions, levels and outcomes, written out literally.

Templates and reports show these labels; keeping the keys literal lets the catalogue test
check that every one exists in Dutch and English.
"""

from app.domain.audit import Outcome
from app.domain.authorization import ApiScope, Level, Permission, Role

ROLE_LABELS: dict[Role, str] = {
    Role.SUPERUSER: "authz.role.superuser",
    Role.ORG_ADMIN: "authz.role.org_admin",
    Role.GROUP_ADMIN: "authz.role.group_admin",
    Role.AUDITOR: "authz.role.auditor",
    Role.AGENT: "authz.role.agent",
}

LEVEL_LABELS: dict[Level, str] = {
    Level.GLOBAL: "authz.level.global",
    Level.ORGANISATION: "authz.level.organisation",
    Level.GROUP: "authz.level.group",
}

PERMISSION_LABELS: dict[Permission, str] = {
    Permission.DASHBOARD_VIEW: "authz.permission.dashboard_view",
    Permission.RIGHTS_VIEW: "authz.permission.rights_view",
    Permission.MATRIX_VIEW: "authz.permission.matrix_view",
    Permission.ORGANISATION_MANAGE: "authz.permission.organisation_manage",
    Permission.ORGANISATION_VIEW: "authz.permission.organisation_view",
    Permission.AUDIT_VIEW: "authz.permission.audit_view",
    Permission.AUDIT_EXPORT: "authz.permission.audit_export",
    Permission.DIAGNOSTICS_MANAGE: "authz.permission.diagnostics_manage",
    Permission.RETENTION_VIEW: "authz.permission.retention_view",
    Permission.STAFF_VIEW: "authz.permission.staff_view",
    Permission.EMBEDDING_MANAGE: "authz.permission.embedding_manage",
    Permission.QUEUE_WORK: "authz.permission.queue_work",
    Permission.INTEGRATIONS_MANAGE: "authz.permission.integrations_manage",
    Permission.BRANDING_MANAGE: "authz.permission.branding_manage",
    Permission.HOLIDAYS_MANAGE: "authz.permission.holidays_manage",
    Permission.HOLIDAYS_ASSIGN: "authz.permission.holidays_assign",
    Permission.GROUP_CREATE: "authz.permission.group_create",
    Permission.GROUP_VIEW: "authz.permission.group_view",
    Permission.GROUP_EDIT: "authz.permission.group_edit",
    Permission.SCHEDULE_MANAGE: "authz.permission.schedule_manage",
    Permission.TEXTS_MANAGE: "authz.permission.texts_manage",
    Permission.REMINDERS_MANAGE: "authz.permission.reminders_manage",
    Permission.BOOKINGS_VIEW: "authz.permission.bookings_view",
    Permission.BOOKINGS_MANAGE: "authz.permission.bookings_manage",
    Permission.CLOSURE_MANAGE: "authz.permission.closure_manage",
    Permission.ORGANISATION_CLOSURE: "authz.permission.organisation_closure",
    Permission.API_CLIENTS_MANAGE: "authz.permission.api_clients_manage",
    Permission.SYSTEM_UPDATE: "authz.permission.system_update",
    Permission.ROLES_MANAGE: "authz.permission.roles_manage",
    Permission.SYSTEM_MAINTENANCE: "authz.permission.system_maintenance",
    Permission.SETTINGS_PLATFORM_VIEW: "authz.permission.settings_platform_view",
    Permission.SETTINGS_PLATFORM_MANAGE: "authz.permission.settings_platform_manage",
    Permission.SETTINGS_LOGIN_MANAGE: "authz.permission.settings_login_manage",
    Permission.SETTINGS_ORGANISATION_MANAGE: "authz.permission.settings_organisation_manage",
    Permission.TEXTS_ORGANISATION_MANAGE: "authz.permission.texts_organisation_manage",
    Permission.IDP_MAPPING_VIEW: "authz.permission.idp_mapping_view",
    Permission.IDP_MAPPING_MANAGE: "authz.permission.idp_mapping_manage",
    Permission.IDENTITY_PROVIDERS_VIEW: "authz.permission.identity_providers_view",
    Permission.IDENTITY_PROVIDERS_MANAGE: "authz.permission.identity_providers_manage",
}

API_SCOPE_LABELS: dict[ApiScope, str] = {
    ApiScope.AVAILABILITY_READ: "authz.api_scope.availability_read",
}

PARTICIPANT_LABELS: dict[str, str] = {
    "participant.availability_view": "authz.citizen.availability_view",
    "participant.book": "authz.citizen.book",
    "participant.booking_view": "authz.citizen.booking_view",
    "participant.booking_change": "authz.citizen.booking_change",
    "participant.booking_cancel": "authz.citizen.booking_cancel",
    "participant.suspended_booking_change": "authz.citizen.suspended_booking_change",
}

CONDITION_LABELS: dict[str, str] = {
    "public": "authz.condition.public",
    "management_link": "authz.condition.management_link",
    "management_link_before_deadline": "authz.condition.management_link_before_deadline",
    "management_link_while_suspended": "authz.condition.management_link_while_suspended",
}

OUTCOME_LABELS: dict[Outcome, str] = {
    Outcome.SUCCESS: "audit.outcome.success",
    Outcome.DENIED: "audit.outcome.denied",
    Outcome.FAILURE: "audit.outcome.failure",
    Outcome.RETRY: "audit.outcome.retry",
    Outcome.SKIPPED: "audit.outcome.skipped",
}
