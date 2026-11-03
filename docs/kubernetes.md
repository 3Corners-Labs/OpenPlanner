# OpenPlanner on Kubernetes (trial, ADR 0114)

A second way to run the same signed images: one pod on a Kubernetes cluster, installed with a
Helm chart. Meant for hosting OpenPlanner externally without a server of your own. It is a
**trial**: written against the Kubernetes API and TransIP's published facts, and tested by
rendering the chart; the first real cluster run is step 3 of the ADR.

## What you get, and what you do not

- One pod with `web` and `worker` on one volume. **One replica, never more**: the database is a
  single SQLite file with one writer. A node failure restarts the pod elsewhere after the volume
  detaches (minutes). For availability beyond that, see ADR 0036.
- The cluster's own ingress and certificates instead of the bundled Caddy.
- The login of your own identity provider (`OIDC_*`); the bundled Keycloak is for hosts
  (`docs/install.md`).
- Updates with `helm upgrade`, not from the portal's *Updates* page.

## What you need

- A cluster with a default `ReadWriteOnce` block storage class (TransIP: `transip-fast-storage`),
  an ingress controller and, for HTTPS, cert-manager or a certificate of your own. TransIP's
  documentation shows a `LoadBalancer` Service and no ingress controller: install one
  (for example ingress-nginx) or check what the control panel offers.
- `kubectl` and Helm 3, and the chart from this repository (`deploy/helm/openplanner`).
- A public name for the installation, DNS pointing at the ingress, and the settings of
  `.env.example` for production: the login, the mail server, `PUBLIC_BASE_URL`.
- The image: the public package `ghcr.io/3corners-labs/openplanner-public` has the minor
  releases (`0.4.0`, `0.5.0`). A patch release is only in the private package
  `ghcr.io/3corners-labs/openplanner`: create an image pull secret with a token that has
  `read:packages` and set `imagePullSecrets` (ADR 0108, ADR 0114 point 9).

## Install

1. Create a namespace and the Secret with the four secrets (generate them; never reuse a value):

   ```sh
   kubectl create namespace openplanner
   kubectl -n openplanner create secret generic openplanner-secrets \
     --from-literal=SETTINGS_ENCRYPTION_KEY="$(openssl rand -base64 48)" \
     --from-literal=MANAGEMENT_TOKEN_KEY="$(openssl rand -base64 48)" \
     --from-literal=SESSION_SECRET="$(openssl rand -base64 48)" \
     --from-literal=OIDC_CLIENT_SECRET='<the client secret from your identity provider>'
   ```

   Back these four values up apart from the database: without `SETTINGS_ENCRYPTION_KEY` the
   stored integration secrets cannot be read (runbook).

2. Write a values file (`openplanner.values.yaml`):

   ```yaml
   image:
     tag: "0.4.0"            # or digest: sha256:...
   publicBaseUrl: https://planner.example.nl
   existingSecret: openplanner-secrets
   forwardedAllowIps: 10.244.0.0/16     # the ingress controller's pods, not the whole cluster
   env:
     ENVIRONMENT: prod
     OIDC_DISCOVERY_URL: https://login.example.nl/.well-known/openid-configuration
     OIDC_CLIENT_ID: openplanner-admin
     SMTP_HOST: smtp.example.nl
     SMTP_PORT: "587"
     MAIL_FROM: OpenPlanner <planner@example.nl>
   ingress:
     className: nginx
     host: planner.example.nl
     tlsSecretName: openplanner-tls       # filled by cert-manager
     annotations:
       cert-manager.io/cluster-issuer: letsencrypt
   persistence:
     size: 5Gi
     storageClass: transip-fast-storage   # leave empty for the cluster's default
   ```

   Find the right `forwardedAllowIps` with
   `kubectl -n ingress-nginx get pods -o wide` (the pods' addresses) or the pod CIDR.

3. Install and check:

   ```sh
   helm install openplanner deploy/helm/openplanner -n openplanner -f openplanner.values.yaml
   kubectl -n openplanner rollout status deployment/openplanner
   kubectl -n openplanner exec deploy/openplanner -c web -- python -m app.cli version
   ```

   The first start runs the database migrations; the pod becomes ready when `/readyz` answers.
   Then open `https://<host>/admin/`. What to do next (organisations, groups, integrations) is in
   `docs/runbook.md`.

## Upgrade

```sh
helm upgrade openplanner deploy/helm/openplanner -n openplanner -f openplanner.values.yaml \
  --set image.tag=0.5.0
```

The pod is replaced (`Recreate`): a few minutes without service, as the database is migrated by
the new version. Before a minor version, take a volume snapshot or copy of the database (below).
Verify the signature of what you deploy (`docs/runbook.md`, *Verifying the image signature*; for
public images, *As an outsider*), preferably by digest.

To know when to upgrade: the release page on GitHub, or `UPDATE_REPOSITORY` in `env`
(`3Corners-Labs/OpenPlanner`) so the portal shows that a newer version exists. Its *Update now*
button does nothing on Kubernetes yet (no updater runs; ADR 0114 step 4). A GitOps tool
(Argo CD, Flux) can follow the chart and the image tag; that is yours to set up, not shipped.

## Backup and restore

The database is the file on the volume (`/srv/data`). Take **volume snapshots** if your provider
offers them, or copy it while the pod runs, using SQLite's own backup (consistent):

```sh
kubectl -n openplanner exec deploy/openplanner -c web -- \
  python -c "import sqlite3; s=sqlite3.connect('/srv/data/booking.db'); d=sqlite3.connect('/srv/data/manual-backup.db'); s.backup(d)"
kubectl -n openplanner cp -c web openplanner/$(kubectl -n openplanner get pod -l app.kubernetes.io/name=openplanner -o name | cut -d/ -f2):/srv/data/manual-backup.db ./manual-backup.db
```

Keep the copy encrypted and away from the cluster; it holds personal data until the retention
purge has run (`docs/dataflow.md`). Test a restore on a second namespace before you rely on it.
`helm uninstall` keeps the volume claim on purpose.

## TransIP notes (to check on your cluster)

- Storage classes `transip-fast-storage` (NVMe, default) and `transip-big-storage` (HDD); use
  the fast one. Volumes are `ReadWriteOnce`, one node at a time; at most 15 per node.
- No ingress controller is documented: install one, and either point a `LoadBalancer` Service of
  that controller at DNS or use TransIP's HA-IP as the documentation describes.
- Not documented: volume snapshots, the Kubernetes versions on offer and the pod security level.
  Note what you find here after the trial.

## Troubleshooting

| You see | Cause and fix |
|---|---|
| `helm install` stops with *… is required* | one of `image.tag`, `publicBaseUrl`, `existingSecret`, `forwardedAllowIps`, `ingress.host` is missing |
| Pod `Pending` | the claim has no volume: `kubectl describe pvc`, the storage class |
| `web` restarts, log says a secret file is unreadable | a key is missing in the Secret, or its name differs from `secretKeys` |
| Everyone appears as the ingress address in the logs | `forwardedAllowIps` does not contain the ingress pods |
| *database is locked* | a second pod on the volume: it must be one replica, and `Recreate` |
| Login redirects fail | `publicBaseUrl` or the provider's redirect address differs from the real name |
