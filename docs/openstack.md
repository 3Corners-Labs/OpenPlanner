# OpenPlanner on OpenStack (one virtual machine)

OpenStack is infrastructure, not a platform of its own for this application: the supported way to
run OpenPlanner there is **one virtual machine that runs the host installation** of
`docs/install.md` (Docker Compose, the bundled HTTPS proxy, the host updater). Nothing in the
application or the installer changes. This page says what to ask the OpenStack operator, how to
set the instance up, and what to check before participants use it.

It is a **trial path**: written from OpenStack's standard services (Nova, Neutron, Cinder,
Glance) and not yet run on a real cloud. The first run writes its findings into *Findings of the
first run* below. Kubernetes on OpenStack (Magnum or a cluster of your own) is a different route:
the chart of `docs/kubernetes.md`, with the storage and load balancer notes there.

## What you get, and what you do not

- One VM with `web`, `worker`, the proxy and, by default, the bundled Keycloak. **One VM, one
  database file, one writer** (ADR 0036). A VM failure means the VM is restarted or rebuilt from
  its volume; that takes minutes, not seconds.
- Updates from the portal, with signature check and rollback (the host updater, ADR 0029).
- Let's Encrypt on the instance's public name, or an own certificate (runbook, *HTTPS
  certificate*).
- Not on offer: more than one VM, a shared file system for the database (Manila, CephFS, NFS):
  SQLite needs local block storage.

## What to ask the OpenStack operator

| Need | Why | Check |
|---|---|---|
| A public address (floating IP) and a DNS name for it | participants and the identity provider reach it by name; Let's Encrypt needs ports 80 and 443 | `A` record (and `AAAA` if the cloud gives IPv6) points at the floating IP |
| Security group: inbound TCP 80 and 443 from anywhere; 22 from the operators' networks only | nothing else is public; `web` listens on the loopback and the proxy's network only | `openstack security group rule list <group>` |
| Security group: outbound limited to the table *Outbound connections and the hop to web* in `docs/dataflow.md`, plus DNS, NTP and the installer's hosts (`docs/install.md`, *Prerequisites*) | the network control behind ASVS V13.2.5 (`docs/assurance/hosting.md`); the application's own guard is a second line | rules for IPv4 and IPv6 alike; a test from inside `web` to a host that is not listed fails |
| A flavor of at least 2 vCPU, 4 GB RAM; 4 vCPU and 8 GB with the bundled identity provider and a busy day | web, worker, proxy, Keycloak and its PostgreSQL share the VM | `openstack flavor show` |
| A **Cinder volume** for the data, attached to the VM, from a volume type with **encryption at rest** | the database holds personal data; the project control PI-08 asks for encryption with a key destroyed with the volume | `openstack volume type show` (encryption), `cryptsetup status` or the provider's statement |
| Cinder snapshots or backups, with a retention not longer than the one in the DPIA, and a copy that leaves the cloud | a snapshot holds personal data as long as the database does (ADR 0102 point 8; disaster recovery is the hosting party's, open point O7) | schedule and retention in writing |
| Outbound SMTP allowed | the confirmation and reminder mails; some clouds block ports 25, 465 and 587 by default | `nc -vz <mail server> 587` from the VM |
| An image: Ubuntu 24.04 LTS (cloud image in Glance) | the installer's supported system | `openstack image list` |
| Named holders of access to the project, the console and the volume | they hold the keys to the installation (`docs/assurance/hosting.md`, *Production hardening*) | the list, in the sign-off table |

## Setting it up

1. **Create the VM** from the Ubuntu 24.04 image, with the flavor above, one network with a
   router to the external network, the security group above, an SSH key, and a floating IP.
2. **Put the data on the Cinder volume.** Create it from the encrypted type, attach it, and mount
   it where Docker keeps its data, before the installer runs:

   ```sh
   sudo mkfs.ext4 -L openplanner /dev/vdb          # the device name differs; check with lsblk
   sudo mkdir -p /var/lib/docker
   echo 'LABEL=openplanner /var/lib/docker ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
   sudo mount /var/lib/docker
   ```

   The volumes `openplanner_data` (the SQLite database and `data/backups`), the proxy's
   certificate and the identity provider's database then all live on it, and one snapshot of one
   volume covers them (take it at a quiet moment, or take the application's own copy first, below).
   The root disk holds the system only.
3. **Check the clock and the metadata path.** `timedatectl` must show the clock synchronised
   (the log sources need one clock, ASVS V16.2.2). Code in the instance can reach the cloud's
   metadata service at `169.254.169.254`; nothing of OpenPlanner needs it. A security group does
   not filter that link-local address on every cloud, so block it from the containers with a rule
   on the host (for example in the `DOCKER-USER` chain), and leave the VM itself alone while
   cloud-init needs it.
4. **Install** as `docs/install.md` says, as the operator account, never as root:

   ```sh
   git clone --branch vX.Y.Z https://github.com/3Corners-Labs/OpenPlanner.git ~/openplanner
   cd ~/openplanner
   ./scripts/install.sh --mode test
   ```

   Use `--mode test` for a user test on its own public name: the same proxy and bundled login as
   production, with candidates allowed (`docs/install.md`, *Modes*). The modes trust different
   signatures, so for real participants install `--mode production` on a fresh VM instead of
   changing the mode of this one.
5. **Behind a load balancer of the cloud** (Octavia or a hardware one) instead of a floating IP
   on the VM: add `--no-tls` if it ends TLS, or keep the bundled proxy and name the balancer
   with `--trusted-proxies '<its addresses>'` (ADR 0129). Without that, every visitor looks like
   the balancer and the rate limits count them as one. The balancer's health check is
   `GET /readyz` on the application.
6. **Run the checks** of the next section and keep their output with the date and version.

## Checks before participants use it

- `docker compose --env-file .env.update exec web python -m app.cli address-check`: the public
  name answers, the proxy is trusted, the application sees `https` (ADR 0086).
- `docker compose --env-file .env.update exec web python -m app.cli tls-check`: a trusted
  certificate with days left. To stay clear of Let's Encrypt's rate limits, install the first
  time with the installer option `--acme-staging` (`docs/install.md`).
- *System status* in the portal shows version, certificate, address and the log destination.
- **MTU.** Overlay networks in OpenStack often carry less than 1500 bytes (1450 is common). A
  container network with a larger MTU then fails only for large packets: mail or the identity
  provider's discovery hangs while small requests work. Compare `ip link` on the VM with
  `docker compose --env-file .env.update exec web cat /sys/class/net/eth0/mtu`; if the
  containers' value is larger, set the networks' MTU in `docker-compose.override.yml`
  (`driver_opts: com.docker.network.driver.mtu`) and recreate them.
- **A test booking** from outside: the confirmation mail arrives, the management link works, the
  call page opens (`docs/runbook.md`).
- **IPv6.** Only with an `AAAA` record: Docker Engine 27 or newer and the cloud routing IPv6 to
  the floating address; otherwise leave the `AAAA` out and say so in the go-live record
  (`docs/runbook.md`, *Visitors over IPv6*).
- **The assurance checklist** `docs/assurance/hosting.md`: the evidence for transport security,
  egress, time, logs, and the two project controls (volume encryption, snapshot retention).

## Backup and restore

Follow the runbook (*Backup and restore*, *Restoring a copy by hand*); the volume is the one at
`/var/lib/docker`. Before an upgrade, take the application's own consistent copy and then a
Cinder snapshot:

```sh
cd ~/openplanner
docker compose --env-file .env.update exec web python -m app.cli backup-db before-upgrade
openstack volume snapshot create --volume <volume> --force before-upgrade   # --force: attached
```

`--force` snapshots an attached volume; the database file is consistent only because the
application's copy was just made, so restore **that copy** (it sits in `data/backups` on the same
volume) and not the live file. The same volume also holds the bundled identity provider's
PostgreSQL and the proxy's certificate; a forced snapshot is only crash-consistent for those, so
restoring the identity provider from it is a crash recovery (the runbook, *Bundled identity
provider*, says what its own backup is). Delete the snapshot once the upgrade has proved itself: it holds
personal data. A copy that leaves the cloud is the hosting party's under ADR 0102 point 8:
encrypted, a fixed term, named access, a destruction record, a tested restore. **Test a restore
on a second VM before go-live.**

## Findings of the first run

To be written by the person who runs it on a real cloud. Note the answers to:

- Does the volume type encrypt, and who holds the key?
- Are SMTP ports open outbound, and is IPv6 routed?
- Which MTU do the instances get, and did the containers need a change?
- Does `--force` give a usable snapshot, and how long does a restore take on a second VM?
- Is the clock synchronised by default, and which NTP source?
- Who patches the hypervisor and the network, and what is the cloud's audit log of API calls?
