from collections.abc import Collection

from app.domain.staff_directory import DirectoryError, DirectoryUser, NewStaffMember, StaffAction


class FakeStaffDirectory:
    """StaffDirectory fake: people in memory, every mail recorded, ``fail`` to break it."""

    def __init__(self, users: Collection[DirectoryUser] = ()) -> None:
        self.users: dict[str, DirectoryUser] = {u.id: u for u in users}
        self.mails: list[tuple[str, frozenset[StaffAction], int]] = []
        self.fail: str | None = None
        self._next = 1

    def _check(self) -> None:
        if self.fail:
            raise DirectoryError(self.fail)

    async def reachable(self) -> bool:
        return self.fail is None

    async def list_users(self) -> list[DirectoryUser]:
        self._check()
        return list(self.users.values())

    async def get_user(self, user_id: str) -> DirectoryUser | None:
        self._check()
        return self.users.get(user_id)

    async def create_user(self, new: NewStaffMember) -> DirectoryUser:
        self._check()
        if any(u.email == new.email for u in self.users.values()):
            raise DirectoryError("conflict")
        user = DirectoryUser(
            f"kc-{self._next}", new.email, new.email, new.first_name, new.last_name
        )
        self._next += 1
        self.users[user.id] = user
        return user

    async def set_entitlements(self, user_id: str, values: Collection[str]) -> None:
        self._check()
        user = self.users.get(user_id)
        if user is None:
            raise DirectoryError("not_found")
        self.users[user_id] = DirectoryUser(
            user.id,
            user.username,
            user.email,
            user.first_name,
            user.last_name,
            user.enabled,
            frozenset(values),
        )

    async def set_enabled(self, user_id: str, enabled: bool) -> None:
        self._check()
        user = self.users.get(user_id)
        if user is None:
            raise DirectoryError("not_found")
        self.users[user_id] = DirectoryUser(
            user.id,
            user.username,
            user.email,
            user.first_name,
            user.last_name,
            enabled,
            user.entitlements,
        )

    async def send_actions_email(
        self, user_id: str, actions: Collection[StaffAction], lifespan_seconds: int
    ) -> None:
        self._check()
        if user_id not in self.users:
            raise DirectoryError("not_found")
        self.mails.append((user_id, frozenset(actions), lifespan_seconds))
