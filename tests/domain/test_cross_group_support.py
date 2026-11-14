"""Tests for the per-booking introspection helper of ``tests.support.cross_group``."""

from tests.support.cross_group import booking_id_methods, is_mutmut_member


class _Service:
    async def cancel(self, principal: object, booking_id: int) -> None:
        """Act on one booking."""

    async def overview(self, principal: object, slug: str) -> None:
        """Act on no booking."""

    async def _private(self, booking_id: int) -> None:
        """Not a use case."""

    async def xǁ_Serviceǁcancel__mutmut_orig(self, principal: object, booking_id: int) -> None:  # noqa: N802
        """What mutmut keeps of the original."""

    async def xǁ_Serviceǁcancel__mutmut_10(self, principal: object, booking_id: int) -> None:  # noqa: N802
        """One mutant."""


def test_booking_id_methods_lists_the_use_cases_on_one_booking() -> None:
    """Only the public methods that take a ``booking_id`` count."""
    assert booking_id_methods(_Service) == {"cancel"}


def test_booking_id_methods_still_sees_a_use_case_that_is_missing() -> None:
    """The list is what a test compares its own with, so a new use case must show up."""

    class Extended(_Service):
        async def reinstate(self, principal: object, booking_id: int) -> None:
            """A use case added later."""

    assert booking_id_methods(Extended) == {"cancel", "reinstate"}


def test_mutmut_generated_names_are_recognised() -> None:
    """The names of the trampoline's original and of every mutant are generated, others are not."""
    assert is_mutmut_member("xǁ_Serviceǁcancel__mutmut_orig")
    assert is_mutmut_member("xǁ_Serviceǁcancel__mutmut_10")
    assert not is_mutmut_member("cancel")
    assert not is_mutmut_member("reinstate_booking")
