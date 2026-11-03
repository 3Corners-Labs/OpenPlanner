# 0114. A Kubernetes trial: one replica, a Helm chart, updates by the cluster's own tooling

Status: accepted (2026-11-03), requested by the product owner for the 0.5.0 release
candidates. Partly built: steps 1 to 3 in this PR; the trial on a cluster and the update
signal are open. Extends ADR 0036 (scaling envelope: still one writer on one SQLite file) and
ADR 0108 (public images); sits beside ADR 0050 (the host installer) as a second way to run the
same signed images.

## Context

The product owner wants to try hosting OpenPlanner on Kubernetes, first on TransIP's managed
Kubernetes (<https://www.transip.nl/kubernetes/>), to host it externally without rebuilding it.
Horizontal scaling is a later question (ADR 0036, possibly 0.6.0).

What TransIP states (page and knowledge base entry 6452, read 2026-11-03): a managed control
plane at no charge; nodes from a shared 1 vCPU / 1 GB class to a dedicated 32 vCPU class; block
storage through the classes `transip-fast-storage` (NVMe, the default) and `transip-big-storage`
(HDD) as `ReadWriteOnce` claims, in 1 GB steps, at most 15 volumes per node; a `LoadBalancer`
Service for exposure; data in the Netherlands. **Not stated, so to verify on the cluster:** an
ingress controller or cert-manager (the knowledge base shows only a `LoadBalancer` Service),
snapshots or backups of volumes, the Kubernetes versions offered, and the pod security level.

What the application is: `web` and `worker` share one SQLite file in WAL mode (ADR 0002, 0036).
SQLite has one writer per file, on local block storage; two pods on the file, even for the
seconds of a rolling update, risk a corrupted or locked database. The images already run as uid
10001 with a read-only root filesystem and dropped capabilities, read settings from the
environment and `X_FILE` files (ADR 0042 section 9), log JSON on stdout and answer `/healthz`
and `/readyz`. What does not carry over: the host installer and the host updater (systemd,
`docker compose`), the bundled HTTPS proxy (Caddy) and the bundled Keycloak.

## Decision

1. **One replica, always.** The chart has no `replicas` value; the Deployment is `replicas: 1`
   with `strategy: Recreate`, and `web` and `worker` are two containers of that one pod, so a
   `ReadWriteOnce` volume serves both. Scaling out is ADR 0036's work and not offered here.
2. **A Helm chart in `deploy/helm/openplanner/`**, plain Helm templates, no operator, no
   dependency on a chart repository. It sets the same hardening as `docker-compose.yml`
   (non-root, read-only root filesystem, no capabilities, no service-account token, a memory
   `/tmp`) and the same start command (`alembic upgrade head`, then `uvicorn`; only `web`
   migrates).
3. **Secrets as files.** The four bootstrap secrets come from a Secret the operator creates
   (`existingSecret`), mounted read-only and passed as `X_FILE`; no secret value is a chart
   value or sits in the pod's environment. Other settings are plain `env` values (class B).
4. **The proxy is the cluster's.** The chart brings an `Ingress` (and an optional
   `NetworkPolicy`) instead of Caddy. `forwardedAllowIps` is required: the ingress controller's
   pods, because `web` trusts `X-Forwarded-For` only from there (ADR 0030); `*` needs the
   explicit `allowAnyForwarder: true`. Between ingress and pod, HTTPS is available through
   `webTls` (ADR 0091 point 3); the decision whether the in-cluster hop must be encrypted in a
   given installation is recorded as the exception ADR 0091 asks for.
5. **The login is the organisation's own.** No bundled Keycloak on Kubernetes in this trial
   (ADR 0090 is for test and lab hosts): `OIDC_*` point at the organisation's provider.
6. **Updates are the cluster's, not the portal's.** `helm upgrade --set image.tag=X.Y.Z`
   (or `image.digest`), by hand, by CI or by a GitOps tool. The image signature is verified by
   the operator (`cosign verify`, runbook) or by an admission policy; the host updater does not
   exist here. The hourly release check may stay on (`UPDATE_REPOSITORY`) so the portal still
   shows that a newer version exists; its *Update now* button then creates a request nobody
   claims. Hiding that button on Kubernetes is a follow-up (step 4).
7. **Volume rules.** Block storage only, never network file storage for SQLite; the claim
   carries `helm.sh/resource-policy: keep`; size 5 Gi by default. The application's own backup
   (ADR 0029, 0036) is not enough on a cluster: the operator takes volume snapshots if the
   provider offers them, or copies the database with `python -m app.cli` (runbook), and tests a
   restore.
8. **Not supported here:** more than one replica, OpenShift (random uid, `Route`), the bundled
   Keycloak, the portal-managed certificate sources (ADR 0070), the internal CA (ADR 0112).
   Each needs its own step.
9. **The public images only give minor releases.** ADR 0108 publishes `vX.Y.0` only. A patch
   release (a security fix) is in the private package: a cluster that must follow patches needs
   a pull secret for it (`imagePullSecrets`), or ADR 0108 changes. The chart supports both.

## Settings

None in the application. The chart values are deployment parameters (class B, ADR 0042: tied
to where the installation runs): `image.*`, `publicBaseUrl`, `forwardedAllowIps`, `env`,
`existingSecret`, `persistence.*`, `ingress.*`, `webTls.*`, `networkPolicy.*`, `resources.*`.
Defaults are in `deploy/helm/openplanner/values.yaml`; the three that must be set (`image.tag`
or `digest`, `publicBaseUrl`, `existingSecret`, with `forwardedAllowIps`) fail the render when
missing, so nothing runs on a guess.

**Fixed on purpose:** one replica and the `Recreate` strategy (a second writer corrupts SQLite);
uid 10001 and the read-only root filesystem (the image's own contract); the probe paths.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR accepted; TransIP facts read | #495 | in PR |
| 1 | `deploy/helm/openplanner/` chart, rendering tests | #495 | in PR |
| 2 | `docs/kubernetes.md`: install, upgrade, backup, TransIP notes; `docs/install.md` pointer | #495 | in PR |
| 3 | Trial on TransIP: ingress and certificates, volume snapshot, a real upgrade and a restore; findings written into `docs/kubernetes.md` (a person with a cluster) | | planned |
| 4 | A deployment-shape setting (class B) that hides *Update now* and says "updated by your cluster" | | planned |
| 5 | The chart published as an OCI artifact next to each release, signed (`helm install oci://ghcr.io/...`) | | planned |

## Consequences

- A second way to run the same signed images, with the same data model and the same one-writer
  limit. High availability is not on offer: a node failure means the pod restarts elsewhere
  once the volume detaches, which can take minutes.
- The chart is tested by rendering (`helm lint`, `helm template`), not by a cluster in CI.
  Step 3 is where a real cluster tests it; until then it is a trial.
- Nothing in `app/` changes in this decision.
