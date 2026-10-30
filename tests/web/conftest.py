"""The admin site's fixtures, for the pages shared by the portal and the citizen pages."""

from tests.admin.conftest import admin_db, site  # noqa: F401 - re-exported fixtures

__all__ = ["admin_db", "site"]
