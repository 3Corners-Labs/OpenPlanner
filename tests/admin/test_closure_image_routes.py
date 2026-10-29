"""The image of a closure (ADR 0037 §3, CL-3): upload, text alternative, removal, public page.

Abuse cases (docs/threat-model.md): an HTML or SVG file named ``.png``, a file above the limit
(413, refused unread), an image without a text alternative, metadata with a location, and
another organisation's admin. The CSP of the closure page stays ``img-src 'self'``.
"""

import re

import httpx

from tests.admin.conftest import GROUP_ADMIN, OUTSIDER, SAME_ORIGIN, Site
from tests.admin.test_admin_flow import audit_events
from tests.admin.test_closure_routes import CLOSURE, UNTIL_SATURDAY, _post
from tests.support.images import SECRET, jpeg, png

IMAGE = f"{CLOSURE}/image"
TEXTS = {"alt_nl": "Een gesloten deur", "alt_en": "A closed door"}


async def _closed(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    response = await _post(site, CLOSURE, UNTIL_SATURDAY)
    assert response.status_code == 303


async def _upload(
    site: Site, data: bytes, values: dict[str, str] | None = None, name: str = "deur.png"
) -> httpx.Response:
    return await site.client.post(
        IMAGE,
        data=TEXTS if values is None else values,
        files={"image": (name, data, "image/png")},
        headers=SAME_ORIGIN,
    )


def _image_src(page: str) -> str:
    match = re.search(r'<img class="closure-image" src="([^"]+)" alt="([^"]*)"', page)
    assert match, "no closure image on the page"
    return match.group(1)


async def test_group_admin_adds_an_image_that_citizens_see_without_metadata(site: Site) -> None:
    await _closed(site)

    response = await _upload(site, jpeg())

    assert response.status_code == 303
    assert response.headers["location"] == f"{CLOSURE}?done=image_saved"
    public = (await site.client.get("/g/demo")).text
    src = _image_src(public)
    assert src.startswith("/g/demo/closure/image?v=")
    assert 'alt="Een gesloten deur"' in public
    assert 'alt="A closed door"' in (await site.client.get("/g/demo?lang=en")).text
    # Heading, notice, image, contact options: in that order (ADR 0037 §5).
    assert public.index("Door een stroomstoring") < public.index('class="closure-image"')

    image = await site.client.get(src)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/jpeg"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert image.headers["content-security-policy"].startswith("default-src 'none'")
    assert SECRET not in image.content
    assert image.content == jpeg(metadata=False)
    assert ("sub-group", "closure.manage", "success", "") in [
        (a, b, str(c), d) for a, b, c, d in await audit_events(site)
    ]

    admin = (await site.client.get(CLOSURE)).text
    assert src in admin  # the admin sees the image they uploaded


async def test_the_closure_page_keeps_its_csp(site: Site) -> None:
    await _closed(site)
    await _upload(site, png())

    page = await site.client.get("/g/demo")

    csp = page.headers["content-security-policy"]
    assert "img-src 'self';" in csp
    assert "http" not in csp


async def test_a_decorative_image_gets_an_empty_alt(site: Site) -> None:
    await _closed(site)

    response = await _upload(site, png(), {"decorative": "yes"})

    assert response.status_code == 303
    public = (await site.client.get("/g/demo")).text
    assert re.search(r'<img class="closure-image" src="[^"]+" alt=""', public)


async def test_the_text_changes_without_a_new_file(site: Site) -> None:
    await _closed(site)
    await _upload(site, png())

    response = await site.client.post(
        IMAGE, data={"alt_nl": "Dichte deur", "alt_en": "Shut door"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 303
    assert 'alt="Dichte deur"' in (await site.client.get("/g/demo")).text


async def test_html_or_svg_named_png_is_refused(site: Site) -> None:
    await _closed(site)

    for fake in (
        b"<!doctype html><script>alert(1)</script>",
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    ):
        response = await _upload(site, fake)
        assert response.status_code == 422
        assert '<a href="#image">' in response.text
        assert "PNG, JPEG of WebP" in response.text
    assert (await site.client.get("/g/demo/closure/image")).status_code == 404


async def test_a_file_above_the_limit_is_refused_unread(site: Site) -> None:
    await _closed(site)

    response = await _upload(site, png() + bytes(600_000))

    assert response.status_code == 413
    assert (await site.client.get("/g/demo/closure/image")).status_code == 404


async def test_an_image_without_a_text_alternative_is_refused(site: Site) -> None:
    await _closed(site)

    response = await _upload(site, png(), {"alt_nl": "Een gesloten deur"})

    assert response.status_code == 422
    assert '<a href="#alt_en">' in response.text
    assert 'value="Een gesloten deur"' in response.text
    assert (await site.client.get("/g/demo/closure/image")).status_code == 404


async def test_no_file_and_no_image_yet_is_explained(site: Site) -> None:
    await _closed(site)

    response = await site.client.post(IMAGE, data=TEXTS, headers=SAME_ORIGIN)

    assert response.status_code == 422
    assert '<a href="#image">' in response.text


async def test_removal_takes_the_image_off_the_page(site: Site) -> None:
    await _closed(site)
    await _upload(site, png())

    response = await site.client.post(f"{IMAGE}/remove", headers=SAME_ORIGIN)

    assert response.headers["location"] == f"{CLOSURE}?done=image_removed"
    assert 'class="closure-image"' not in (await site.client.get("/g/demo")).text
    assert (await site.client.get("/g/demo/closure/image")).status_code == 404


async def test_the_image_of_a_lifted_closure_is_no_longer_public(site: Site) -> None:
    await _closed(site)
    await _upload(site, png())
    src = _image_src((await site.client.get("/g/demo")).text)

    await _post(site, f"{CLOSURE}/lift")

    assert (await site.client.get(src)).status_code == 404


async def test_without_a_closure_there_is_no_image(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    response = await _upload(site, png())

    assert response.status_code == 404
    assert (await site.client.get("/g/demo/closure/image")).status_code == 404
    assert (await site.client.get("/g/unknown/closure/image")).status_code == 404


async def test_another_organisations_admin_is_refused(site: Site) -> None:
    await _closed(site)
    await site.login_as(OUTSIDER)

    upload = await _upload(site, png())
    remove = await site.client.post(f"{IMAGE}/remove", headers=SAME_ORIGIN)

    assert upload.status_code == 403
    assert remove.status_code == 403


async def test_cross_site_uploads_are_refused(site: Site) -> None:
    await _closed(site)

    response = await site.client.post(
        IMAGE,
        data=TEXTS,
        files={"image": ("deur.png", png(), "image/png")},
        headers={"sec-fetch-site": "cross-site"},
    )

    assert response.status_code == 403
