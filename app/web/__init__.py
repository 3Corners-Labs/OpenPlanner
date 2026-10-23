"""Shared HTML plumbing for the server-rendered entry points (public pages and admin UI).

Sits between the entry points and the adapters: ``app.api`` and ``app.admin`` both use it,
so request locale resolution, template setup and HTML security headers exist once.
"""
