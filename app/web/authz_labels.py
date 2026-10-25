"""Catalogue keys for roles, permissions, levels and outcomes, written out literally.

Templates and reports show these labels; keeping the keys literal lets the catalogue test
check that every one exists in Dutch and English.
"""

from app.domain.audit import Outcome
from app.domain.authorization import Level, Permission, Role

ROLE_LABELS: dict[Role, str] = {
    Role.SUPERUSER: "authz.role.superuser",
    Role.ORG_ADMIN: "authz.role.org_admin",
    Role.GROUP_ADMIN: "authz.role.group_admin",
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
    Permission.AUDIT_VIEW: "authz.permission.audit_view",
    Permission.INTEGRATIONS_MANAGE: "authz.permission.integrations_manage",
    Permission.BRANDING_MANAGE: "authz.permission.branding_manage",
    Permission.GROUP_CREATE: "authz.permission.group_create",
    Permission.GROUP_VIEW: "authz.permission.group_view",
    Permission.GROUP_EDIT: "authz.permission.group_edit",
    Permission.SCHEDULE_MANAGE: "authz.permission.schedule_manage",
    Permission.TEXTS_MANAGE: "authz.permission.texts_manage",
    Permission.REMINDERS_MANAGE: "authz.permission.reminders_manage",
    Permission.BOOKINGS_VIEW: "authz.permission.bookings_view",
}

CITIZEN_LABELS: dict[str, str] = {
    "citizen.availability_view": "authz.citizen.availability_view",
    "citizen.book": "authz.citizen.book",
    "citizen.booking_view": "authz.citizen.booking_view",
    "citizen.booking_change": "authz.citizen.booking_change",
    "citizen.booking_cancel": "authz.citizen.booking_cancel",
}

CONDITION_LABELS: dict[str, str] = {
    "public": "authz.condition.public",
    "management_link": "authz.condition.management_link",
    "management_link_before_deadline": "authz.condition.management_link_before_deadline",
}

OUTCOME_LABELS: dict[Outcome, str] = {
    Outcome.SUCCESS: "audit.outcome.success",
    Outcome.DENIED: "audit.outcome.denied",
    Outcome.FAILURE: "audit.outcome.failure",
}
