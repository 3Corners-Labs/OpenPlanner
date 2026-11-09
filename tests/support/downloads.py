"""The admin downloads, pinned for the download tests: who sets the header, which are audited."""

from app.domain.audit_export import EXPORT_EVENT
from app.domain.idp_mapping import EXPORTED_EVENT
from app.domain.usage_export_service import EXPORT_EVENT as REPORT_EXPORT_EVENT

DOWNLOAD_MODULES = {
    "adapters/configuration_json.py",
    "admin/audit_routes.py",
    "admin/branding_routes.py",
    "admin/idp_mapping_routes.py",
    "admin/settings_routes.py",
    "admin/usage_routes.py",
}
"""Every module that sets a ``content-disposition`` header; a new download is added here."""

AUDITED_DOWNLOADS = {
    "/admin/audit/export.csv": EXPORT_EVENT,
    "/admin/idp-mapping/export.txt": EXPORTED_EVENT,
    "/admin/organisations/{org}/usage/export.csv": REPORT_EXPORT_EVENT,
    "/admin/groups/{slug}/usage/export.csv": REPORT_EXPORT_EVENT,
}
"""Every admin ``GET`` route that writes an audit event when it is opened, by route path, with
that event's action. Each refuses a request another site makes (``SameOrigin``, ASVS V3.5.3);
the privacy sweep opens every admin ``GET`` route and fails on one that writes an event and is
not here."""


def address(route: str) -> str:
    """Return the address of a listed download in the test site: the demo organisation and group."""
    return route.format(org="demo", slug="demo")
