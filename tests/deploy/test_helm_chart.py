"""The Helm chart of the Kubernetes trial (ADR 0114): what it renders, and what it refuses."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).parents[2] / "deploy" / "helm" / "openplanner"
HELM = shutil.which("helm")
pytestmark = pytest.mark.skipif(HELM is None, reason="helm is not installed")

REQUIRED = {
    "image.tag": "0.4.0",
    "publicBaseUrl": "https://planner.example.nl",
    "existingSecret": "openplanner-secrets",
    "forwardedAllowIps": "10.244.0.0/16",
    "ingress.host": "planner.example.nl",
}


def _template(
    json_values: dict[str, object] | None = None, **overrides: str
) -> subprocess.CompletedProcess[str]:
    values = {**REQUIRED, **overrides}
    args = [arg for key, value in values.items() for arg in ("--set", f"{key}={value}")]
    for key, value in (json_values or {}).items():
        args += ["--set-json", f"{key}={json.dumps(value)}"]
    assert HELM
    return subprocess.run(  # noqa: S603
        [HELM, "template", "t", str(CHART), *args], capture_output=True, text=True, check=False
    )


def _docs(json_values: dict[str, object] | None = None, **overrides: str) -> dict[str, dict]:  # type: ignore[type-arg]
    result = _template(json_values, **overrides)
    assert result.returncode == 0, result.stderr
    return {d["kind"]: d for d in yaml.safe_load_all(result.stdout) if d}


def test_the_chart_passes_helm_lint() -> None:
    assert HELM
    args = [arg for key, value in REQUIRED.items() for arg in ("--set", f"{key}={value}")]
    lint = subprocess.run(  # noqa: S603
        [HELM, "lint", str(CHART), *args], capture_output=True, text=True, check=False
    )
    assert lint.returncode == 0, lint.stdout


def test_it_is_one_pod_that_replaces_itself() -> None:
    deployment = _docs()["Deployment"]
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}
    names = [c["name"] for c in deployment["spec"]["template"]["spec"]["containers"]]
    assert names == ["web", "worker"]


def test_every_container_keeps_the_images_hardening() -> None:
    pod = _docs()["Deployment"]["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsUser"] == 10001
    assert pod["automountServiceAccountToken"] is False
    for container in pod["containers"]:
        context = container["securityContext"]
        assert context["readOnlyRootFilesystem"] is True
        assert context["allowPrivilegeEscalation"] is False
        assert context["capabilities"] == {"drop": ["ALL"]}


def test_secrets_are_files_never_values() -> None:
    pod = _docs()["Deployment"]["spec"]["template"]["spec"]
    for container in pod["containers"]:
        env = {e["name"]: e.get("value") for e in container["env"]}
        for key in ("SETTINGS_ENCRYPTION_KEY", "MANAGEMENT_TOKEN_KEY", "SESSION_SECRET"):
            assert env[f"{key}_FILE"] == f"/run/secrets/{key}"
            assert key not in env
    secret = next(v for v in pod["volumes"] if v["name"] == "secrets")["secret"]
    assert secret["secretName"] == "openplanner-secrets"
    assert secret["defaultMode"] == 0o400


def test_only_web_migrates_and_only_web_listens() -> None:
    containers = {
        c["name"]: c for c in _docs()["Deployment"]["spec"]["template"]["spec"]["containers"]
    }
    assert "alembic upgrade head" in " ".join(containers["web"]["command"])
    assert containers["worker"]["command"] == ["python", "-m", "app.jobs.worker"]
    assert "ports" not in containers["worker"]
    assert containers["web"]["readinessProbe"]["httpGet"]["path"] == "/readyz"


def test_the_claim_survives_an_uninstall_and_is_one_writer() -> None:
    claim = _docs()["PersistentVolumeClaim"]
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert claim["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"


def test_web_tls_switches_the_probes_to_https() -> None:
    web = _docs(**{"webTls.enabled": "true", "webTls.secretName": "w"})["Deployment"]["spec"][
        "template"
    ]["spec"]["containers"][0]
    assert web["readinessProbe"]["httpGet"]["scheme"] == "HTTPS"
    assert {"WEB_TLS_CERT_FILE", "WEB_TLS_KEY_FILE"} <= {e["name"] for e in web["env"]}


@pytest.mark.parametrize(
    "missing", ["image.tag", "publicBaseUrl", "existingSecret", "ingress.host"]
)
def test_a_missing_required_value_fails_the_render(missing: str) -> None:
    values = {k: v for k, v in REQUIRED.items() if k != missing}
    args = [arg for key, value in values.items() for arg in ("--set", f"{key}={value}")]
    assert HELM
    result = subprocess.run(  # noqa: S603
        [HELM, "template", "t", str(CHART), *args], capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "required" in result.stderr


def test_trusting_every_forwarder_must_be_said_aloud() -> None:
    assert _template(forwardedAllowIps="").returncode != 0
    assert _template(forwardedAllowIps="", allowAnyForwarder="true").returncode == 0


POLICY_ON = {"networkPolicy.enabled": "true"}
INGRESS_NAMESPACE = {
    "networkPolicy.ingressNamespaceLabels": {"kubernetes.io/metadata.name": "edge"}
}
MAIL_SERVER = {"cidr": "198.51.100.7/32", "ports": [587, 465]}
PROVIDER = {"cidr": "203.0.113.0/24", "ports": [443]}


def _policy(**json_values: object) -> dict:  # type: ignore[type-arg]
    return _docs({**INGRESS_NAMESPACE, **json_values}, **POLICY_ON)["NetworkPolicy"]["spec"]


def test_the_network_policy_names_the_ingress_namespace_or_refuses() -> None:
    # An empty selector would admit every namespace, and a guessed controller blocks everyone.
    result = _template(**POLICY_ON)
    assert result.returncode != 0
    assert "networkPolicy.ingressNamespaceLabels is required" in result.stderr


def test_without_egress_the_policy_guards_the_way_in_only() -> None:
    spec = _policy()
    assert spec["policyTypes"] == ["Ingress"]
    assert "egress" not in spec
    allowed = spec["ingress"][0]["from"][0]["namespaceSelector"]["matchLabels"]
    assert allowed == {"kubernetes.io/metadata.name": "edge"}


def test_egress_allows_the_cluster_dns_and_the_listed_destinations_only() -> None:
    spec = _policy(
        **{"networkPolicy.egress.enabled": True, "networkPolicy.egress.to": [MAIL_SERVER, PROVIDER]}
    )
    assert spec["policyTypes"] == ["Ingress", "Egress"]
    dns, mail, provider = spec["egress"]
    assert dns["to"] == [
        {
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
            "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
        }
    ]
    assert dns["ports"] == [{"port": 53, "protocol": "UDP"}, {"port": 53, "protocol": "TCP"}]
    assert mail == {
        "to": [{"ipBlock": {"cidr": "198.51.100.7/32"}}],
        "ports": [{"port": 587, "protocol": "TCP"}, {"port": 465, "protocol": "TCP"}],
    }
    assert provider["ports"] == [{"port": 443, "protocol": "TCP"}]


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"networkPolicy.egress.to": []}, "networkPolicy.egress.to is required"),
        ({"networkPolicy.egress.to": [{"ports": [443]}]}, "needs a cidr"),
        ({"networkPolicy.egress.to": [{"cidr": "203.0.113.0/24"}]}, "needs ports"),
        (
            {"networkPolicy.egress.to": [PROVIDER], "networkPolicy.egress.dns.podLabels": None},
            "networkPolicy.egress.dns",
        ),
    ],
)
def test_egress_refuses_to_guess_where_the_pod_may_go(
    values: dict[str, object], message: str
) -> None:
    result = _template(
        {**INGRESS_NAMESPACE, "networkPolicy.egress.enabled": True, **values}, **POLICY_ON
    )
    assert result.returncode != 0
    assert message in result.stderr
