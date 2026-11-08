"""The admin downloads, pinned for the download tests: who sets the header, which are audited."""

from app.domain.audit_export import EXPORT_EVENT
from app.domain.idp_mapping import EXPORTED_EVENT

DOWNLOAD_MODULES = {
    "admin/audit_routes.py",
    "admin/branding_routes.py",
    "admin/configuration_routes.py",
    "admin/idp_mapping_routes.py",
    "admin/settings_routes.py",
    "api/agent/routes.py",
}
"""Every module that sets a ``content-disposition`` header; a new download is added here."""

AUDITED_DOWNLOADS = {
    "/admin/audit/export.csv": EXPORT_EVENT,
    "/admin/idp-mapping/export.txt": EXPORTED_EVENT,
}
"""Every admin ``GET`` route that writes an audit event when it is opened, with that event's
action. Each refuses a request another site makes (``SameOrigin``, ASVS V3.5.3); the privacy
sweep opens every admin ``GET`` route and fails on one that writes an event and is not here."""
