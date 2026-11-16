# 0130. The chart runs under OpenShift's restricted-v2 security context

Status: accepted (2026-11-16), requested by the product owner after reading the container
platform manifesto of the hosting platform. Partly built: steps 0 and 1 in the pull request that
adds this ADR; see *Build status*. Changes point 8 of ADR 0114 (OpenShift "not supported here")
and extends its chart.

## Context

The hosting platform's manifesto states that every cluster enforces the security context
constraint `restricted-v2` by default. That constraint assigns the user id and the `fsGroup` from
the range of the namespace and rejects a pod that names a value outside it. The chart of ADR 0114
fixes `runAsUser`, `runAsGroup` and `fsGroup` at 10001, so on such a cluster the pod would be
refused. The rest of the hardening already satisfies the constraint: non-root, no privilege
escalation, all capabilities dropped, read-only root filesystem, seccomp `RuntimeDefault`.

A random user id belongs to group 0 only. The image's writable paths (`/srv/data`,
`/srv/portal-certs`) belonged to uid 10001 and were not writable for group 0.

The manifesto also asks for several replicas, a PodDisruptionBudget, GitOps delivery and a
database outside the pod. Those are not decided here: SQLite on one volume stays (ADR 0002,
ADR 0036), so one replica stays.

## Decision

1. **`openshift.enabled` in the chart** (class B, ADR 0042: tied to where the installation runs).
   When true, the pod names no `runAsUser`, `runAsGroup` or `fsGroup`; the platform assigns them.
   Everything else of the hardening stays, in both modes.
2. **A `Route` replaces the `Ingress`** when `openshift.enabled` is true: edge termination with
   a redirect from http. With `webTls.enabled` the route re-encrypts and
   `openshift.route.destinationCACertificate` is required, so the render fails rather than
   guesses.
3. **The image's writable paths are writable for group 0** (`chgrp 0`, `chmod g=u`), for every
   deployment shape. The owner stays uid 10001, so the host installation and Compose are
   unchanged.
4. **Not changed:** one replica and `Recreate` (ADR 0114 point 1), the probes, the secrets as
   files. The manifesto's replicas and PodDisruptionBudget need a database that allows more than
   one writer and are a separate ADR (ADR 0036).

## Settings

None in the application. `openshift.enabled` (default false) and `openshift.route.destinationCACertificate`
are chart values, class B (ADR 0042), listed in `deploy/helm/openplanner/values.yaml`.

**Fixed on purpose:** the rest of the container hardening (it is what the constraint requires),
and uid 10001 as the image's own user outside OpenShift.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR | #911 | merged |
| 1 | `openshift.enabled`, the Route, group-0 writable paths, rendering tests, `docs/kubernetes.md` section | #911 | merged |
| 2 | A run on a real OpenShift cluster under `restricted-v2` (a person with a cluster) | | planned |
| 3 | An ArgoCD example (`Application` for the chart), once the chart is an OCI artifact (ADR 0114 step 5) | | planned |

## Consequences

- The chart can be admitted under `restricted-v2`, tested by rendering only until step 2.
- The same image serves OpenShift and every other shape; no second image.
- High availability beyond one pod stays out of reach until a separate ADR on a multi-writer
  database; the platform's rolling upgrades restart the pod, with the downtime ADR 0114 names.
