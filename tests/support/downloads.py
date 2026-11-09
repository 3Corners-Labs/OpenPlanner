"""The places in ``app/`` that set a download header, pinned for the download tests."""

DOWNLOAD_MODULES = {
    "admin/audit_routes.py",
    "admin/branding_routes.py",
    "admin/configuration_routes.py",
    "admin/idp_mapping_routes.py",
    "admin/settings_routes.py",
    "admin/usage_routes.py",
    "api/agent/routes.py",
}
"""Every module that sets a ``content-disposition`` header; a new download is added here."""
