"""The address of a staff member comes from the bundled provider's directory (ADR 0117)."""

from app.adapters.keycloak_directory import DirectoryAddresses
from app.domain.staff_directory import DirectoryError, DirectoryUser


class _Directory:
    def __init__(self, users: dict[str, DirectoryUser], broken: bool = False) -> None:
        self._users = users
        self._broken = broken

    async def get_user(self, user_id: str) -> DirectoryUser | None:
        if self._broken:
            raise DirectoryError("unreachable")
        return self._users.get(user_id)


def _user(email: str = "olga@voorbeeld.nl", *, enabled: bool = True) -> DirectoryUser:
    return DirectoryUser("u1", "olga", email, "Olga", "Test", enabled)


async def test_a_person_the_directory_holds_has_an_address() -> None:
    addresses = DirectoryAddresses(_Directory({"u1": _user()}))  # type: ignore[arg-type]

    assert await addresses.address_of("u1") == "olga@voorbeeld.nl"


async def test_no_directory_an_unknown_person_a_disabled_one_or_a_broken_provider_has_none() -> (
    None
):
    assert await DirectoryAddresses(None).address_of("u1") is None
    assert await DirectoryAddresses(_Directory({})).address_of("u1") is None  # type: ignore[arg-type]
    disabled = DirectoryAddresses(_Directory({"u1": _user(enabled=False)}))  # type: ignore[arg-type]
    assert await disabled.address_of("u1") is None
    assert await DirectoryAddresses(_Directory({}, broken=True)).address_of("u1") is None  # type: ignore[arg-type]


async def test_only_the_bundled_providers_own_people_are_looked_up_and_the_id_is_quoted() -> None:
    asked: list[str] = []

    class Spy(_Directory):
        async def get_user(self, user_id: str) -> DirectoryUser | None:
            asked.append(user_id)
            return await super().get_user(user_id)

    addresses = DirectoryAddresses(Spy({"u%2F..%2Fother": _user()}))  # type: ignore[arg-type]

    assert await addresses.address_of("idp:org-a:someone") is None
    assert await addresses.address_of("u/../other") == "olga@voorbeeld.nl"
    assert asked == ["u%2F..%2Fother"]
