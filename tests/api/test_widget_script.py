"""The widget script route (ADR 0035 §5): CORS-readable, cross-origin resource, SRI hash."""

import base64
import hashlib
from pathlib import Path

import httpx
import pytest

from app.web import widget_bundle
from app.web.widget_bundle import load_bundle

SCRIPT = b"customElements.define('gov-booking', class extends HTMLElement {});\n"


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "gov-booking.js"
    path.write_bytes(SCRIPT)
    monkeypatch.setattr(widget_bundle, "BUNDLE_PATH", path)
    return path


async def test_the_script_is_served_for_any_host_page(
    client: httpx.AsyncClient, bundle: Path
) -> None:
    response = await client.get(
        "/widget/v1/gov-booking.js", headers={"origin": "https://www.gemeente.example"}
    )

    assert response.status_code == 200
    assert response.content == SCRIPT
    assert response.headers["content-type"] == "text/javascript; charset=utf-8"
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers
    assert response.headers["cross-origin-resource-policy"] == "cross-origin"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_without_a_build_the_script_is_not_found(
    client: httpx.AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(widget_bundle, "BUNDLE_PATH", tmp_path / "missing.js")

    assert (await client.get("/widget/v1/gov-booking.js")).status_code == 404


def test_the_integrity_is_the_sha384_of_the_file(bundle: Path) -> None:
    found = load_bundle()

    assert found is not None
    expected = base64.b64encode(hashlib.sha384(SCRIPT).digest()).decode()
    assert found.integrity == f"sha384-{expected}"

    bundle.write_bytes(SCRIPT + b"// v2\n")
    changed = load_bundle()
    assert changed is not None
    assert changed.integrity != found.integrity
