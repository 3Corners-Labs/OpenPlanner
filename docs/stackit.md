# OpenPlanner on STACKIT (one virtual machine)

STACKIT's Compute Engine runs virtual machines, so the route is the one of `docs/openstack.md`:
**one VM with the host installation of `docs/install.md`**. This page holds only what differs.
Read that page first for the sizing, the setup steps, the checks before a user test, and the
backup order; read the Kubernetes route (STACKIT Kubernetes Engine, `docs/kubernetes.md`) only
when one VM is no longer enough, and note that it keeps the one-replica limit of ADR 0114.

It is a **trial path**, written from STACKIT's published documentation and service certificates,
read on 15 November 2026. Nothing here has been run on a STACKIT project yet; the first run
writes its findings at the end.

## What the documentation says

| Topic | What STACKIT states | What it means here |
|---|---|---|
| Encryption at rest | Block Storage volumes and snapshots, and Backup Storage, are encrypted on the system side with AES-256 (service certificate for Block Storage). **Ephemeral disks are the exception.** | Put `/var/lib/docker` on a **block volume**, never on the VM's ephemeral disk, or the database is not covered (project control PI-08 in `docs/assurance/hosting.md`). Customer-held keys are not documented: ask whether they exist. |
| Snapshots | Taken of a volume or of a whole server (one per attached volume). **Not restorable in place**: a snapshot creates a new volume or a new server. They sit on the same storage backend as the volume. | The restore of `docs/runbook.md` (*Restoring a copy by hand*) starts from a **new** volume made from the snapshot, attached to a new or the same VM. A snapshot is no disaster-recovery copy. |
| Backups | A separate product, *Backup Storage*, logically and physically apart from Block Storage: full and incremental backups of a volume. Volumes are not backed up automatically. | The copy that leaves the volume (ADR 0102 point 8) is Backup Storage or a copy of your own; set the schedule and the retention, and keep them within the DPIA's. |
| Firewall | Security groups, defined once and reused for many servers. | One group for the web tier: inbound 80 and 443, 22 from the operators' networks, outbound as the table in `docs/dataflow.md` (ASVS V13.2.5). |

## What to ask STACKIT, because the documentation does not say

- Are outbound SMTP ports (25, 465, 587) open from a new server, and how is an unblock asked?
- Is IPv6 available on the server's public address? If not, leave the `AAAA` record out and write
  the reason in the go-live record (`docs/runbook.md`, *Visitors over IPv6*).
- Can the volume be encrypted with a key of your own, and who can read the platform's keys?
- Which MTU do servers get? Compare it with the containers' (`docs/openstack.md`, *Checks*).
- Is the clock synchronised by default, and from which source (ASVS V16.2.2)?
- Who has access to the project, and is the audit log of API calls available to you?
- The region and the data location, for the DPIA.

## Setting it up

1. Create a project, a network and the security group above, an SSH key and a server from an
   Ubuntu 24.04 image (sizes: `docs/openstack.md`), with a public IP and a DNS name.
2. Create a **block volume**, attach it, format it, and mount it at `/var/lib/docker` before the
   installer runs (the commands are in `docs/openstack.md`, *Setting it up*, step 2).
3. Install as `docs/install.md` says, `--mode test` for a user test on its own name.
4. Before an upgrade: `python -m app.cli backup-db`, then a snapshot of the volume. Delete the
   snapshot when the upgrade has proved itself; it holds personal data as long as the database does.
5. **Test a restore from a snapshot on a second server** before go-live: make a volume from the
   snapshot, attach it, follow *Restoring a copy by hand*. Write down how long it took.

## Minimal beta (beta.openplanner.nl)

For a beta with **test data only**, no real participants and no restore requirement, the setup
shrinks to this. It drops the snapshot, backup and restore steps above; it keeps the rest,
because those protect the people who try it.

1. One server (2 vCPU, 4 GB; Ubuntu 24.04), one public IP, and the security group: inbound 80
   and 443 from anywhere, 22 from your own address only.
2. DNS: an `A` record `beta.openplanner.nl` to the public IP (at the registrar of the zone; a
   `CAA` record, if any, must allow `letsencrypt.org`).
3. Install with the bundled login and the HTTPS proxy:

   ```sh
   git clone --branch vX.Y.Z https://github.com/3Corners-Labs/OpenPlanner.git ~/openplanner
   cd ~/openplanner
   ./scripts/install.sh --mode test --tls-domain beta.openplanner.nl --acme-staging
   ```

   Run it first with `--acme-staging`, check `address-check`, then run it again without the
   option for the real certificate (`docs/runbook.md`, *HTTPS certificate*).
4. A mail server for the confirmation mails (`SMTP_HOST` and the rest, asked by the installer):
   testers must receive the confirmation and management links. A `lab` installation would keep
   all mail in Mailpit, so it is no use for end users. If STACKIT blocks the mail ports,
   use the SMTP relay of a mail provider on port 587.
5. Check: `docker compose --env-file .env.update exec web python -m app.cli address-check`,
   *System status* in the portal, and one test booking from another network.

What the beta leaves out on purpose: the volume is the server's own disk (STACKIT does not
encrypt ephemeral disks), there is no backup, and a lost server means a new installation. So
**no real personal data in it**: testers use made-up names and addresses. Before real
participants, follow the full setup above (block volume, backup, a tested restore) on a new server.

## Findings of the first run

To be written by the person who runs it:

- The answers to the questions above, with the date and who at STACKIT gave them.
- Did a server's volume come up encrypted, as the service certificate says?
- Restore time from a snapshot, and the Backup Storage schedule and retention.
- Anything the installer or the proxy needed that this page does not say.
