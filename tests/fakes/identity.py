from collections.abc import Mapping

from app.domain.authorization import Principal
from app.domain.entitlements import LoginClaims
from app.domain.ports import IdentityError, LoginStart, LoginTestReport
from tests.support.principals import legacy_claims


class FakeIdentityProvider:
    """IdentityProvider fake: returns ``next_claims`` or fails with ``fail``.

    ``next_principal`` is the convenient way to set it: the legacy claims that resolve to
    that admin. ``next_claims`` takes exact claims (entitlement values, ambiguous legacy
    claims) for the tests of ADR 0049.
    """

    def __init__(self) -> None:
        self.next_claims: LoginClaims | None = None
        self.fail: str | None = None
        self.redirect_uris: list[str] = []
        self.fresh: list[bool] = []
        self.trial_claims: LoginClaims | None = None
        self.test_report: LoginTestReport | None = None
        """What ``finish_test_login`` reports (ADR 0083 point 9); default from ``next_claims``."""

    @property
    def next_principal(self) -> Principal | None:
        return None

    @next_principal.setter
    def next_principal(self, admin: Principal | None) -> None:
        self.next_claims = legacy_claims(admin) if admin is not None else None

    @property
    def trial_principal(self) -> Principal | None:
        return None

    @trial_principal.setter
    def trial_principal(self, admin: Principal | None) -> None:
        self.trial_claims = legacy_claims(admin) if admin is not None else None

    async def start_login(self, redirect_uri: str, *, fresh: bool = False) -> LoginStart:
        self.redirect_uris.append(redirect_uri)
        self.fresh.append(fresh)
        return LoginStart("https://idp.test/authorize?x=1", "state-1", "nonce-1", "verifier-1")

    async def finish_login(
        self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str
    ) -> LoginClaims:
        assert (code_verifier, nonce) == ("verifier-1", "nonce-1")
        if self.fail:
            raise IdentityError(self.fail)
        assert self.next_claims is not None
        return self.next_claims

    async def finish_trial_login(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        nonce: str,
        trial: Mapping[str, str],
    ) -> tuple[LoginClaims, LoginClaims]:
        current = await self.finish_login(
            code=code, redirect_uri=redirect_uri, code_verifier=code_verifier, nonce=nonce
        )
        return current, self.trial_claims or current

    async def finish_test_login(
        self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str
    ) -> LoginTestReport:
        claims = await self.finish_login(
            code=code, redirect_uri=redirect_uri, code_verifier=code_verifier, nonce=nonce
        )
        if self.test_report is not None:
            return self.test_report
        return LoginTestReport(claims, ("email", "email_verified", "sub"), True, "example.test")

    async def logout_url(self, post_logout_redirect_uri: str) -> str | None:
        return f"https://idp.test/logout?post_logout_redirect_uri={post_logout_redirect_uri}"
