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
  an ingress controller and, for HTTPS, cert-manager or a certificate of your own. Use a
  maintained controller, for example Traefik: the Kubernetes project retired ingress-nginx in
  March 2026, and it gets no security fixes any more. TransIP includes no controller (*TransIP
  notes* below).
- `kubectl` and Helm 3, and the chart from this repository (`deploy/helm/openplanner`).
- A public name for the installation, DNS pointing at the ingress, and the settings of
  `.env.example` for production: the login, the mail server, `PUBLIC_BASE_URL`.
- The image: the public package `ghcr.io/3corners-labs/openplanner-public` has every release:
  the minor releases from `0.4.0` and the patches from the first patch after ADR 0116 step 6
  (ADR 0108, ADR 0116 point 10). Only a release candidate is in the private package
  `ghcr.io/3corners-labs/openplanner` alone: for one, create an image pull secret with a token
  that has `read:packages` and set `imagePullSecrets`.

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
     className: traefik
     host: planner.example.nl
     tlsSecretName: openplanner-tls       # filled by cert-manager
     annotations:
       cert-manager.io/cluster-issuer: letsencrypt
   persistence:
     size: 5Gi
     storageClass: transip-fast-storage   # leave empty for the cluster's default
   networkPolicy:
     enabled: true
     ingressNamespaceLabels:              # the controller's namespace; required, no default
       kubernetes.io/metadata.name: traefik
   ```

   Find the right `forwardedAllowIps` with
   `kubectl -n <the controller's namespace> get pods -o wide` (the pods' addresses) or the pod
   CIDR. Limiting the way out as well is *Outbound traffic* below.

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
public images, *As an outsider*), preferably by digest, or let the cluster refuse what is not
signed (*Signed images only*).

To know when to upgrade: the release page on GitHub, or `UPDATE_REPOSITORY` in `env`
(`3Corners-Labs/OpenPlanner`) so the portal shows that a newer version exists. Its *Update now*
button does nothing on Kubernetes yet (no updater runs; ADR 0114 step 4). A GitOps tool
(Argo CD, Flux) can follow the chart and the image tag; that is yours to set up, not shipped.

## Signed images only

On a host, the updater verifies every image before it runs (ADR 0006). A cluster has no updater,
so let it check at the door: an admission policy that refuses a pod whose image is not signed by
the `main` workflow of this repository. The identity is the installer's for its `production`
mode (`scripts/install.sh`, `identity_regexp`): release tags only. A test cluster that follows
release candidates also accepts `refs/heads/main`, as test and lab hosts do (ADR 0067).

With the Sigstore policy-controller installed, label the namespace
(`kubectl label namespace openplanner policy.sigstore.dev/include=true`) and apply:

```yaml
apiVersion: policy.sigstore.dev/v1beta1
kind: ClusterImagePolicy
metadata:
  name: openplanner-signed
spec:
  mode: warn                      # enforce once the checks below pass
  images:
    - glob: "ghcr.io/3corners-labs/openplanner**"
  authorities:
    - keyless:
        url: https://fulcio.sigstore.dev
        identities:
          - issuer: https://token.actions.githubusercontent.com
            subjectRegExp: '^https://github\.com/3Corners-Labs/OpenPlanner/\.github/workflows/main\.yml@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$'
      ctlog:
        url: https://rekor.sigstore.dev
```

Kyverno's `verifyImages` rule takes the same issuer and subject. Either way:

- **Not yet run on a cluster** (ADR 0114 step 3): it follows the controller's documented
  resources; check it before you rely on it.
- CI signs with cosign 3, in the newer Sigstore bundle format (the reason a host needs cosign 3,
  runbook *When the host says the signature is invalid*). Use a policy-controller or Kyverno
  version that verifies that format; an older one finds no signature, so in `enforce` mode it
  refuses every pod.
- Check in `warn` mode first: the running release passes without a warning, and the same policy
  with a wrong identity (for example `@refs/tags/v0\.0\.0$`) warns about it. Then switch to
  `enforce`.
- Deploy by `image.digest`, so the image that was checked is the image that runs.

## Outbound traffic

`networkPolicy.egress` limits where the pod may connect: the cluster DNS, and the address ranges
in `networkPolicy.egress.to`, each with its TCP ports. It is the cluster's form of the egress
rule in `docs/assurance/hosting.md` (V13.2.5); the application's own guard
(`app/adapters/outbound.py`) is a second line, not the network control.

```yaml
networkPolicy:
  enabled: true
  ingressNamespaceLabels:
    kubernetes.io/metadata.name: traefik
  egress:
    enabled: true
    to:
      - {cidr: 198.51.100.7/32, ports: [587]}   # the mail server
      - {cidr: 192.0.2.0/24, ports: [443]}      # the identity provider
      - {cidr: 203.0.113.10/32, ports: [443]}   # the installation's own public address
```

- **What to list:** every row of *Outbound connections and the hop to web* in
  `docs/dataflow.md` that the installation uses: the identity providers, the mail server, the
  SMS provider, Webex and Webex Contact Center, the log destinations, the release check
  (`api.github.com`, when `UPDATE_REPOSITORY` is set), and the public address itself, where the
  worker checks the certificate and the round trip.
- **Addresses, not names:** a network policy matches address ranges. For a service whose
  addresses change (Webex, GitHub), take the ranges its provider publishes, or use a network
  plugin that allows host names (for example Cilium's `toFQDNs`) or an egress proxy.
- **A destination inside the cluster** (for example an identity provider in another namespace)
  is not an address range. Add a NetworkPolicy of your own next to the chart's; the rules of
  policies add up.
- **Outside this policy:** the ingress controller, cert-manager and the load balancer live in
  their own namespaces or at the provider.
- **After switching it on:** log in, open *System status* and make a test booking (the
  confirmation mail). A missing destination shows there as a time-out.
- The chart refuses to render egress without destinations, and refuses an entry without
  `cidr` or `ports`.

## Backup and restore

The database is the file on the volume (`/srv/data`). It holds personal data until the
retention purge has run (`docs/dataflow.md`), and so does every copy of it.

**Before an upgrade**, take a copy on the volume with the application's own command; it is
consistent while the pod runs:

```sh
kubectl -n openplanner exec deploy/openplanner -c web -- python -m app.cli backup-db before-0.5.0
```

The copy lands in `data/backups` next to the database (mode 600), and the worker deletes it after
24 hours, as on a host (ADR 0102). It never leaves the volume.

**Volume snapshots**, where the provider offers them (TransIP: the class `team-blue-k8s`):

```yaml
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: openplanner-before-0-5-0
  namespace: openplanner
spec:
  volumeSnapshotClassName: team-blue-k8s
  source:
    persistentVolumeClaimName: openplanner-data
```

A snapshot is a copy with personal data too: delete it
(`kubectl -n openplanner delete volumesnapshot openplanner-before-0-5-0`) once the upgrade has
proved itself.

**Restoring a snapshot** follows the fixed order of the runbook (*Restoring a copy by hand*):

1. Make a claim from the snapshot:

   ```yaml
   apiVersion: v1
   kind: PersistentVolumeClaim
   metadata:
     name: openplanner-restored
     namespace: openplanner
   spec:
     accessModes: [ReadWriteOnce]
     storageClassName: transip-fast-storage
     resources:
       requests:
         storage: 5Gi
     dataSource:
       apiGroup: snapshot.storage.k8s.io
       kind: VolumeSnapshot
       name: openplanner-before-0-5-0
   ```

2. Start on it in maintenance mode:

   ```sh
   helm upgrade openplanner deploy/helm/openplanner -n openplanner -f openplanner.values.yaml \
     --set persistence.existingClaim=openplanner-restored --set env.MAINTENANCE_MODE=true
   ```

3. The copy is older than the live data, so run the sweep and end the sessions it brought back:

   ```sh
   kubectl -n openplanner exec deploy/openplanner -c web -- python -m app.cli purge-overdue
   kubectl -n openplanner exec deploy/openplanner -c web -- python -m app.cli sessions end-all --reason restore
   ```

4. Run `helm upgrade` again without `env.MAINTENANCE_MODE`, keeping `persistence.existingClaim`.
   Delete the old claim once the restored one holds: it holds personal data as well.

Test a restore in a second namespace before you rely on it. `helm uninstall` keeps the volume
claim on purpose.

**Disaster recovery** (a copy that leaves the cluster) belongs to the hosting party, under ADR
0102 point 8 and, for the hosted models, ADR 0116 point 13. The copy must be:

- encrypted;
- kept for a fixed term, bounded by the retention holds;
- accessible to named people only;
- destroyed with a record of it;
- covered by a tested restore.

It never goes to a personal machine. TransIP says its snapshots do not replace an external
backup, so arrange this before go-live.

## TransIP notes

From TransIP's published documentation, read on 5 November 2026; the trial (ADR 0114 step 3)
confirms or corrects them.

- **Storage.** The classes are `transip-fast-storage` (NVMe, the default) and
  `transip-big-storage` (HDD); use the fast one. Volumes are `ReadWriteOnce`, one node at a time,
  at most 15 per node. Volume snapshots use the class `team-blue-k8s`.
- **Ingress.** None is included.
  - A `LoadBalancer` Service becomes a TransIP HA-IP Pro, billed separately.
  - By default the load balancer hides the visitor: every request seems to come from it, and the
    rate limits count all visitors as one.
  - Switch on the PROXY protocol on both sides: put the annotation
    `service.beta.kubernetes.io/transip-loadbalancer-protocol: "proxy"` on the controller's
    Service, and turn on the PROXY protocol in the controller for the load balancer's addresses
    only (Traefik: `entryPoints.<name>.proxyProtocol.trustedIPs`).
  - TransIP's own guide (knowledge base 7207) shows this with ingress-nginx; the same settings
    exist in a maintained controller.
- **IPv6.** Dual-stack clusters are not supported, so the installation is reachable over IPv4
  only. The internet.nl website test of the go-live (runbook, *Go-live check of the public
  address*) then scores IPv6 below 100%: write that reason in its record.
- **Upgrades** of the cluster come from the control panel, one minor version at a time. TransIP
  publishes no list of the versions on offer.
- **Access.** A TransIP account has one login and no extra users, so whoever holds it holds the
  cluster. Name those people as `docs/assurance/hosting.md` asks for host access.
- **Not documented, so ask TransIP or check on the cluster:**
  - encryption at rest of the volumes, and of the cluster's own store (etcd, where the Secret
    lives);
  - the network plugin: whether a NetworkPolicy is enforced, and whether egress can be limited
    by host name;
  - whether the outbound mail ports (25, 465 and 587) are blocked on nodes, as they are on a new
    VPS;
  - audit logs of the Kubernetes API;
  - an SLA;
  - who patches the nodes' operating system.

  Note the answers here after the trial.

## Troubleshooting

| You see | Cause and fix |
|---|---|
| `helm install` stops with *… is required* | one of `image.tag`, `publicBaseUrl`, `existingSecret`, `forwardedAllowIps`, `ingress.host` is missing; with `networkPolicy.enabled`, also `networkPolicy.ingressNamespaceLabels` (`kubectl get namespace <name> --show-labels`) |
| Every visitor appears as one address that is not an ingress pod; bookings hit the rate limit early | the load balancer hides the visitor: switch on the PROXY protocol (*TransIP notes*) |
| With `networkPolicy.egress` on, the login, mail or a *System status* check times out | a destination is missing from `networkPolicy.egress.to` (*Outbound traffic*) |
| A pod is refused by the admission controller | the image is not signed by `main.yml`, the policy's identity is too narrow, or the verifier cannot read cosign 3's bundles (*Signed images only*) |
| Pod `Pending` | the claim has no volume: `kubectl describe pvc`, the storage class |
| `web` restarts, log says a secret file is unreadable | a key is missing in the Secret, or its name differs from `secretKeys` |
| Everyone appears as the ingress address in the logs | `forwardedAllowIps` does not contain the ingress pods |
| *database is locked* | a second pod on the volume: it must be one replica, and `Recreate` |
| Login redirects fail | `publicBaseUrl` or the provider's redirect address differs from the real name |
