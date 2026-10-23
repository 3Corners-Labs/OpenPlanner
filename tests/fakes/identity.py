from app.domain.authorization import Principal
from app.domain.ports import IdentityError, LoginStart


class FakeIdentityProvider:
    """IdentityProvider fake: returns ``next_principal`` or fails with ``fail``."""

    def __init__(self) -> None:
        self.next_principal: Principal | None = None
        self.fail: str | None = None
        self.redirect_uris: list[str] = []

    async def start_login(self, redirect_uri: str) -> LoginStart:
        self.redirect_uris.append(redirect_uri)
        return LoginStart("https://idp.test/authorize?x=1", "state-1", "nonce-1", "verifier-1")

    async def finish_login(
        self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str
    ) -> Principal:
        assert (code_verifier, nonce) == ("verifier-1", "nonce-1")
        if self.fail:
            raise IdentityError(self.fail)
        assert self.next_principal is not None
        return self.next_principal
