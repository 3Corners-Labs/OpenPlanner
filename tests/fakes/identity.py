from collections.abc import Mapping

from app.domain.authorization import Principal
from app.domain.ports import IdentityError, LoginStart


class FakeIdentityProvider:
    """IdentityProvider fake: returns ``next_principal`` or fails with ``fail``."""

    def __init__(self) -> None:
        self.next_principal: Principal | None = None
        self.fail: str | None = None
        self.redirect_uris: list[str] = []
        self.fresh: list[bool] = []
        self.trial_principal: Principal | None = None

    async def start_login(self, redirect_uri: str, *, fresh: bool = False) -> LoginStart:
        self.redirect_uris.append(redirect_uri)
        self.fresh.append(fresh)
        return LoginStart("https://idp.test/authorize?x=1", "state-1", "nonce-1", "verifier-1")

    async def finish_login(
        self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str
    ) -> Principal:
        assert (code_verifier, nonce) == ("verifier-1", "nonce-1")
        if self.fail:
            raise IdentityError(self.fail)
        assert self.next_principal is not None
        return self.next_principal

    async def finish_trial_login(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        nonce: str,
        trial: Mapping[str, str],
    ) -> tuple[Principal, Principal]:
        current = await self.finish_login(
            code=code, redirect_uri=redirect_uri, code_verifier=code_verifier, nonce=nonce
        )
        return current, self.trial_principal or current

    async def logout_url(self, post_logout_redirect_uri: str) -> str | None:
        return f"https://idp.test/logout?post_logout_redirect_uri={post_logout_redirect_uri}"
