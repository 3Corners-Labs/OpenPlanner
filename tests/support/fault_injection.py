"""Shared by the fault-injection tests (ASVS V16.5): the personal data a failure must not show."""

PII = ("ada@example.org", "Lovelace", "+31612345678")
"""Ada's address, last name and phone number: none may appear in a log, an event or an error."""
