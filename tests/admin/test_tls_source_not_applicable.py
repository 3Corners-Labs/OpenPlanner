"""Without the bundled proxy the certificate source does not apply (ADR 0066)."""

from tests.admin.conftest import SAME_ORIGIN, SUPER, Site
from tests.support.certificates import make_pair

URL = "/admin/settings"


async def test_behind_an_own_proxy_the_block_only_says_so(site: Site) -> None:
    await site.login_as(SUPER)

    text = (await site.client.get(URL)).text

    assert "Niet van toepassing: HTTPS eindigt bij een eigen reverse proxy" in text
    assert 'action="/admin/settings/tls/certificate"' not in text
    assert 'action="/admin/settings/tls/check"' in text  # the status is still theirs to refresh


async def test_uploads_and_choices_are_refused(site: Site) -> None:
    await site.login_as(SUPER)
    pair = make_pair()

    uploaded = await site.client.post(
        f"{URL}/tls/certificate",
        files={"chain": ("c", pair.chain), "key": ("k", pair.key)},
        headers=SAME_ORIGIN,
    )
    requested = await site.client.post(f"{URL}/tls/csr", headers=SAME_ORIGIN)
    chosen = await site.client.post(
        f"{URL}/tls_certificate_source", data={"value": "own"}, headers=SAME_ORIGIN
    )

    assert uploaded.status_code == 422
    assert requested.status_code == 422
    assert chosen.status_code == 422
    assert "eigen reverse proxy" in chosen.text
