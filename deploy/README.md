# deploy — running watchman on a real host

Everything in this directory is for the operator who has tried the
[quickstart](../docs/quickstart.md) and now wants the tool *running*, not just
*running once*.

| Artifact | What it is |
|---|---|
| `watchman-collector.service` | systemd unit template for the per-host collector (Layer 1/2/3). |
| `watchman-central.service` | systemd unit template for the central service (ingest, alerting, fleet view). |
| `gen-certs.sh` | Generates the mutual-TLS material for the collector → central hop. |
| `docker/Dockerfile` | Builds all four binaries **and** the shipped rule pack into one distroless image, instead of installing units. |

Both units run the binaries **unprivileged** and with a hardened profile; the
collector needs no root and no capabilities. Every optional telemetry source
degrades instead of failing to start, so a host without eBPF, without Docker, or
even without auditd still runs the unit cleanly (see
[`../docs/deploy-collector.md`](../docs/deploy-collector.md)).

## Install (systemd)

The configs in `examples/` and the units in this directory reference a handful of
host paths under `/etc/watchman` and `/var/lib/watchman`. The steps below create
every one of them, in an order that starts. (Central and the collector both
**fail loudly** — exit 1, not a silent degrade — if a configured path is missing:
that is deliberate, because a service that starts with its telemetry or its TLS
quietly absent is worse than one that refuses to start. The cost of that choice is
that the install has to be complete, which is what this block is for.)

```sh
# 1. A dedicated, unprivileged service account — no login, no home.
sudo useradd --system --no-create-home --shell /usr/sbin/nologin watchman

# 2. Give it the read access it needs (and nothing more):
#    - the audit log (Layer 3 telemetry). Group name varies by distro:
#        Debian/Ubuntu: adm        RHEL/Fedora: systemd-journal
sudo usermod -aG adm watchman

# 3. Binaries and directories.
sudo install -m 0755 bin/watchman-collector /usr/local/bin/
sudo install -m 0755 bin/watchman-central   /usr/local/bin/
# The operator CLIs — needed only if you use Layer 2 (watchman-manifest) or
# pin rule packs (watchman-rules). Install them from the same build so the
# versions match the running service.
sudo install -m 0755 bin/watchman-manifest  /usr/local/bin/
sudo install -m 0755 bin/watchman-rules     /usr/local/bin/
sudo install -d -m 0750 -o watchman -g watchman /etc/watchman
sudo install -d -m 0750 -o watchman -g watchman /var/lib/watchman

# 4. Configs (start from the templates), then edit the paths.
sudo install -m 0640 -o watchman -g watchman examples/collector.yaml /etc/watchman/collector.yaml
sudo install -m 0640 -o watchman -g watchman examples/central.yaml   /etc/watchman/central.yaml

# 5. The rule pack the collector config points at (behavior.rules_dir). The
#    collector REFUSES to start if that directory is missing or empty — a rule
#    engine with no rules is a silent blind spot, so it fails loud. Without this
#    step the collector unit exits with 'stat /etc/watchman/rules: no such file'.
sudo install -d -m 0750 -o watchman -g watchman /etc/watchman/rules
sudo install -m 0644 rules/*.yaml rules/packs.pins /etc/watchman/rules/

# 6. auditd telemetry. The collector config sets behavior.audit_log, and a
#    named-but-absent log is fatal (same blind-spot rule). Install auditd, drop
#    the four -a ... -k watchman lines from docs/quickstart.md §4 into
#    /etc/audit/rules.d/watchman.rules, and load them:
sudo augenrules --load

# 7. TLS material — generate it BEFORE starting central. The shipped central.yaml
#    sets tls:, and a missing cert is a deliberate *fatal* error (a fleet must
#    never silently fall back to plaintext). Write it into /etc/watchman so the
#    config's default paths line up, and hand it to the service account; the
#    permission trap is spelled out under "## TLS" below.
#    Use the SAME name here that collector.yaml sets as report.tls.server_name,
#    or the collector's handshake fails closed.
sudo deploy/gen-certs.sh /etc/watchman central.example
sudo chown watchman:watchman /etc/watchman/ca.crt /etc/watchman/leaf.crt /etc/watchman/leaf.key
sudo chmod 0640 /etc/watchman/leaf.key        # readable by watchman, by no one else

# 8. uid_map — the shipped central.yaml sets uid_map: /etc/watchman/passwd, and a
#    configured-but-absent file is a FATAL config error (not a silent numeric
#    fallback). Copy your fleet's base-image /etc/passwd, or delete the uid_map
#    line from central.yaml if you do not want uids named in alerts.
sudo install -m 0644 /etc/passwd /etc/watchman/passwd

# 9. The units.
sudo install -m 0644 deploy/watchman-central.service   /etc/systemd/system/
sudo install -m 0644 deploy/watchman-collector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now watchman-central
sudo systemctl enable --now watchman-collector
```

The collector hashes the agent workspaces it is configured to watch, so the
`watchman` user must be able to **read** each `agent_roots` path. The units set
`ProtectHome=read-only` for the collector for exactly this reason, and
`ProtectHome=yes` for central (it never needs the workspaces).

> **This path has been run, not just analysed.** Both units were installed and
> started as the unprivileged `watchman` user with mutual TLS on: central came up
> serving mTLS on `:8080`, the collector enrolled an agent root over the
> authenticated hop, and a one-line edit to a persona file produced
> `DRIFT … identity.drift` on the collector and a matching `ALERT: 1 drift …` on
> central on the next poll. Not `systemd-analyze verify` — an actual systemd run,
> unprivileged, end to end. The rule pack, TLS material, and uid_map above are the
> steps that run needed beyond the unit files themselves.

For the service-side decisions behind this install — the two stores, the
mutual-TLS trust boundary, Layer 2 provenance, `uid_map`, and alerting — see
[`../docs/deploy-central.md`](../docs/deploy-central.md). For the collector's
telemetry sources, [`../docs/deploy-collector.md`](../docs/deploy-collector.md).

## What the hardening assumes

The units are written for the common case and are meant to be read, not copied
blindly. Two knobs change if you use the optional sources:

- **eBPF `file.access`** needs `CAP_BPF` + `CAP_PERFMON` (never `CAP_SYS_ADMIN`)
  and a binary built with `-tags ebpf`. Uncomment the `AmbientCapabilities=` /
  `CapabilityBoundingSet=` block in `watchman-collector.service`.
- **The Docker adapter** talks to `/var/run/docker.sock`. Keep `AF_UNIX` in
  `RestrictAddressFamilies` (it is there by default) and make sure the `watchman`
  user is in the `docker` group — or point `behavior.docker` at a socket it can
  read.

If a workspace lives under a path hidden by `ProtectSystem=strict`, grant the
collector read access to it with `ReadOnlyPaths=/that/path` — the collector only
reads there, so it never needs write access.

## TLS

The collector → central hop carries the agent inventory (every persona/skill
file on the host), so it should be authenticated in both directions:

```sh
./deploy/gen-certs.sh /etc/watchman/tls central.example.com
```

Point central's `tls: {cert, key, ca}` and the collector's
`report.tls: {cert, key, ca, server_name}` at the generated files. With TLS
unset both ends speak plain HTTP — fine for a loopback test, not for a fleet.

> **The service must be able to read the keys.** Both units run as
> `User=watchman`, but `gen-certs.sh` writes the private keys mode `0600` owned
> by whoever ran it. Generate them as root (you usually are, to write under
> `/etc/watchman`) and central fails at startup — a **fatal**, not a
> degradation: `tls: mtls: load key pair: open .../leaf.key: permission denied`.
> Hand the material to the service account after generating:
>
> ```sh
> sudo chown -R watchman:watchman /etc/watchman/tls
> sudo chmod 0640 /etc/watchman/tls/*.key   # group-readable; never world-readable
> ```
>
> Or grant it to the group instead of chowning:
> `sudo chgrp -R watchman /etc/watchman/tls && sudo chmod 0640 /etc/watchman/tls/*.key`.
> Either way, the key must be readable by `watchman` and by nobody else. (The
> same constraint applies inside the container, where the reader is uid `65532`
> — see "Mutual TLS in containers" below.)

## Docker instead of systemd

`docker/Dockerfile` builds all four binaries and the shipped rule pack into one
distroless image. It is the lighter path when you already deploy containers; the
systemd units are the lighter path on a plain Linux host. Build from the repo
root:

```sh
docker build -f deploy/docker/Dockerfile -t watchman .
```

The image is unprivileged and has no shell — that is the point. Configs are not
baked in (they are host-specific), so mount them; the rule pack **is** baked in
at `/etc/watchman/rules`, and `/var/lib/watchman` is a volume for the durable
stores:

> **Mount configs at `/config`, not `/etc/watchman`.** A bind mount at
> `/etc/watchman` *shadows* the rule pack the image bakes there, and Layer 3 then
> fails to load — `behavior.rules_dir "/etc/watchman/rules": no such file or
> directory`. Move the mount one level over and the baked pack survives (or point
> `behavior.rules_dir` at wherever you mounted a pack).

```sh
# collector — mount what it reads: config, audit log, agent workspaces
docker run --rm \
  -v /etc/watchman:/config:ro \
  -v /var/log/audit/audit.log:/var/log/audit/audit.log:ro \
  -v /home:/home:ro \
  watchman -config /config/collector.yaml

# central — override the entrypoint, persist the stores
#   (no -p 8080:8080 needed if collectors share a Docker network with it)
docker run --rm -p 8080:8080 \
  -v /etc/watchman:/config:ro \
  -v watchman-state:/var/lib/watchman \
  --entrypoint /watchman-central \
  watchman -config /config/central.yaml
```

Three container-specific caveats: there is no `docker` CLI inside the image, so
the Docker adapter still fires but with **identity only** (no `inspect`
enrichment); the eBPF adapter needs `--cap-add=BPF --cap-add=PERFMON` and an image
built with `-tags ebpf`; and a bind mount at `/etc/watchman` shadows the baked
pack (above). The binaries are compiled `CGO_ENABLED=0` — required for the
distroless (no-libc) runtime, and true to the project's no-CGO promise.

### Mutual TLS in containers

A container fleet should authenticate the collector → central hop, and the flags
are small. Generate material with `deploy/gen-certs.sh <dir> <central-name>` —
the `<central-name>` becomes a SAN, so use the name the collector will dial (the
central container's network alias):

```sh
./deploy/gen-certs.sh ./tls central        # SAN: DNS:central, DNS:localhost, 127.0.0.1
docker network create watchman-net
```

Point central's `tls: {cert, key, ca}` and each collector's
`report.tls: {cert, key, ca, server_name}` at that material (`server_name: central`), mount
the cert directory **separately** from the configs, and put both containers on the
network so the collector can resolve `central`:

```sh
# central — serves mTLS on :8080
docker run -d --name central --network watchman-net \
  -v ./tls:/tls:ro -v watchman-state:/var/lib/watchman \
  --entrypoint /watchman-central \
  watchman -config /config/central.yaml

# collector — reaches central by name over the network
docker run --rm --network watchman-net \
  -v ./tls:/tls:ro \
  -v /home:/home:ro \
  watchman -config /config/collector.yaml -once
```

Two things to get right, both verified end to end:

- **The cert files must be readable by uid `65532`.** The image is distroless
  `nonroot`; `gen-certs.sh` writes keys mode `0600` owned by you, so a mounted
  key is unreadable inside the container. Make the cert directory `0755` and the
  files `0644` (or `chown 65532` them) — the container can then read them.
- **`server_name` must match a SAN.** Dialing `https://central:8080/...` with
  `server_name: central` verifies; a mismatch fails closed with
  `certificate is valid for central, localhost, not <name>`. With `report.tls`
  unset entirely against an mTLS central, the collector fails the handshake —
  and now says so plainly (`report TLS has no client identity`) rather than
  calling an `https://` endpoint "plaintext".
