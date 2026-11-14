"""The scan of a placed SDK file against the call page's policy (ADR 0123 step 4c)."""

import json
from pathlib import Path

import pytest

from app.web.security_policy import CALL_CSP
from scripts.check_sdk_policy import Finding, host_allowed, main, report, scan


@pytest.mark.parametrize(
    ("scheme", "host", "allowed"),
    [
        ("https", "mercury-connection-a.wbx2.com", True),
        ("wss", "mercury-connection-a.wbx2.com", True),
        ("https", "x.webex.com", True),
        ("wss", "x.webex.com", True),
        ("https", "x.ciscospark.com", True),
        # The policy's own rules: subdomains only, the listed schemes only.
        ("https", "wbx2.com", False),
        ("http", "x.webex.com", False),
        ("wss", "x.ciscospark.com", False),
        ("https", "x.webexcontent.com", False),
        ("https", "evilwbx2.com", False),
        ("https", "unpkg.com", False),
    ],
)
def test_a_host_is_allowed_exactly_as_the_connect_source_says(
    scheme: str, host: str, allowed: bool
) -> None:
    assert host_allowed(scheme, host) is allowed


def test_the_hosts_checked_are_the_hosts_of_the_policy() -> None:
    for source in CALL_CSP.split("; "):
        if source.startswith("connect-src"):
            for pattern in source.split()[1:]:
                if "*" in pattern:
                    scheme, _, rest = pattern.partition("://*.")
                    assert host_allowed(scheme, f"a.{rest}")


def test_a_clean_file_has_no_findings() -> None:
    source = (
        'fetch("https://a.wbx2.com/x"); ns="http://www.w3.org/2000/svg"; '
        'var w = new Worker("w.js");'
    )

    assert scan(source) == []


def test_each_thing_the_policy_forbids_is_found_once() -> None:
    source = (
        'u = "https://cdn.example.net/a.js"; v = "https://cdn.example.net/b.js";'
        "new Worker(URL.createObjectURL(new Blob([code])));"
        "WebAssembly.instantiate(bytes);"
        'eval("1"); new Function("return 1");'
    )

    kinds = [(f.kind) for f in scan(source)]

    assert kinds == ["host", "blob_worker", "wasm", "eval", "eval"]
    assert scan(source)[0] == Finding("host", "https://cdn.example.net")


def test_a_method_called_eval_is_not_the_global_one() -> None:
    assert scan("model.eval(1); retrieval(2)") == []


def test_the_report_says_what_only_a_real_meeting_shows() -> None:
    text = report(scan('x = "https://cdn.example.net/";'))

    assert "1 host" in text
    assert "real meeting" in text
    assert "nothing" in report([])


def test_without_a_placed_file_it_is_skipped_with_the_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "app/web/static/vendor/webex"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(
        json.dumps({"name": "webex", "version": "1", "source": "s", "file": "meetings.min.js"})
    )

    assert main(["x"], root=tmp_path) == 0
    assert "not placed" in capsys.readouterr().out


def test_a_given_file_is_scanned_and_a_finding_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "sdk.js"
    bad.write_text('WebAssembly.compile(b); u="https://a.wbx2.com";')
    good = tmp_path / "ok.js"
    good.write_text('u="https://a.wbx2.com";')

    assert main(["x", str(bad)]) == 1
    assert "wasm" in capsys.readouterr().out
    assert main(["x", str(good)]) == 0
    assert main(["x", "a", "b"]) == 2
