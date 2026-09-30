# deploy — running agentids on a real host

Everything in this directory is for the operator who has tried the
[quickstart](../docs/quickstart.md) and now wants the tool *running*, not just
*running once*.

| Artifact | What it is |
|---|---|
| `agentids-collector.service` | systemd unit template for the per-host collector (Layer 1/2/3). |
| `agentids-central.service` | systemd unit template for the central service (ingest, alerting, fleet view). |
| `gen-certs.sh` | Generates the mutual-TLS material for the collector → central hop. |
| `docker/Dockerfile` | Builds all four binaries **and** the shipped rule pack into one distroless image, instead of installing units. |

Both units run the binaries **unprivileged** and with a hardened profile; the
collector needs no root and no capabilities. Every optional telemetry source
degrades instead of failing to start, so a host without eBPF, without Docker, or
even without auditd still runs the unit cleanly (see
[`../docs/deploy-collector.md`](../docs/deploy-collector.md)).

## Install (systemd)

```sh
# 1. A dedicated, unprivileged service account — no login, no home.
sudo useradd --system --no-create-home --shell /usr/sbin/nologin agentids

# 2. Give it the read access it needs (and nothing more):
#    - the audit log (Layer 3 telemetry). Group name varies by distro:
#        Debian/Ubuntu: adm        RHEL/Fedora: systemd-journal
sudo usermod -aG adm agentids

# 3. Binaries and directories.
sudo install -m 0755 bin/agentids-collector /usr/local/bin/
sudo install -m 0755 bin/agentids-central   /usr/local/bin/
sudo install -d -m 0750 -o agentids -g agentids /etc/agentids
sudo install -d -m 0750 -o agentids -g agentids /var/lib/agentids

# 4. Configs (start from the templates), then edit the paths.
sudo install -m 0640 -o agentids -g agentids examples/collector.yaml /etc/agentids/collector.yaml
sudo install -m 0640 -o agentids -g agentids examples/central.yaml   /etc/agentids/central.yaml

# 5. The units.
sudo install -m 0644 deploy/agentids-central.service   /etc/systemd/system/
sudo install -m 0644 deploy/agentids-collector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agentids-central
sudo systemctl enable --now agentids-collector
```

The collector hashes the agent workspaces it is configured to watch, so the
`agentids` user must be able to **read** each `agent_roots` path. The units set
`ProtectHome=read-only` for the collector for exactly this reason, and
`ProtectHome=yes` for central (it never needs the workspaces).

For the service-side decisions behind this install — the two stores, the
mutual-TLS trust boundary, Layer 2 provenance, `uid_map`, and alerting — see
[`../docs/deploy-central.md`](../docs/deploy-central.md). For the collector's
telemetry sources, [`../docs/deploy-collector.md`](../docs/deploy-collector.md).

## What the hardening assumes

The units are written for the common case and are meant to be read, not copied
blindly. Two knobs change if you use the optional sources:

- **eBPF `file.access`** needs `CAP_BPF` + `CAP_PERFMON` (never `CAP_SYS_ADMIN`)
  and a binary built with `-tags ebpf`. Uncomment the `AmbientCapabilities=` /
  `CapabilityBoundingSet=` block in `agentids-collector.service`.
- **The Docker adapter** talks to `/var/run/docker.sock`. Keep `AF_UNIX` in
  `RestrictAddressFamilies` (it is there by default) and make sure the `agentids`
  user is in the `docker` group — or point `behavior.docker` at a socket it can
  read.

If a workspace lives under a path hidden by `ProtectSystem=strict`, grant the
collector read access to it with `ReadOnlyPaths=/that/path` — the collector only
reads there, so it never needs write access.

## TLS

The collector → central hop carries the agent inventory (every persona/skill
file on the host), so it should be authenticated in both directions:

```sh
./deploy/gen-certs.sh /etc/agentids/tls central.example.com
```

Point central's `tls: {cert, key, ca}` and the collector's
`report.tls: {cert, key, ca, server_name}` at the generated files. With TLS
unset both ends speak plain HTTP — fine for a loopback test, not for a fleet.

## Docker instead of systemd

`docker/Dockerfile` builds all four binaries and the shipped rule pack into one
distroless image. It is the lighter path when you already deploy containers; the
systemd units are the lighter path on a plain Linux host. Build from the repo
root:

```sh
docker build -f deploy/docker/Dockerfile -t agentids .
```

The image is unprivileged and has no shell — that is the point. Configs are not
baked in (they are host-specific), so mount them; the rule pack **is** baked in
at `/etc/agentids/rules`, and `/var/lib/agentids` is a volume for the durable
stores:

> **Mount configs at `/config`, not `/etc/agentids`.** A bind mount at
> `/etc/agentids` *shadows* the rule pack the image bakes there, and Layer 3 then
> fails to load — `behavior.rules_dir "/etc/agentids/rules": no such file or
> directory`. Move the mount one level over and the baked pack survives (or point
> `behavior.rules_dir` at wherever you mounted a pack).

```sh
# collector — mount what it reads: config, audit log, agent workspaces
docker run --rm \
  -v /etc/agentids:/config:ro \
  -v /var/log/audit/audit.log:/var/log/audit/audit.log:ro \
  -v /home:/home:ro \
  agentids -config /config/collector.yaml

# central — override the entrypoint, persist the stores
#   (no -p 8080:8080 needed if collectors share a Docker network with it)
docker run --rm -p 8080:8080 \
  -v /etc/agentids:/config:ro \
  -v agentids-state:/var/lib/agentids \
  --entrypoint /agentids-central \
  agentids -config /config/central.yaml
```

Three container-specific caveats: there is no `docker` CLI inside the image, so
the Docker adapter still fires but with **identity only** (no `inspect`
enrichment); the eBPF adapter needs `--cap-add=BPF --cap-add=PERFMON` and an image
built with `-tags ebpf`; and a bind mount at `/etc/agentids` shadows the baked
pack (above). The binaries are compiled `CGO_ENABLED=0` — required for the
distroless (no-libc) runtime, and true to the project's no-CGO promise.

### Mutual TLS in containers

A container fleet should authenticate the collector → central hop, and the flags
are small. Generate material with `deploy/gen-certs.sh <dir> <central-name>` —
the `<central-name>` becomes a SAN, so use the name the collector will dial (the
central container's network alias):

```sh
./deploy/gen-certs.sh ./tls central        # SAN: DNS:central, DNS:localhost, 127.0.0.1
docker network create agentids-net
```

Point central's `tls: {cert, key, ca}` and each collector's
`report.tls: {cert, key, ca, server_name}` at that material (`server_name: central`), mount
the cert directory **separately** from the configs, and put both containers on the
network so the collector can resolve `central`:

```sh
# central — serves mTLS on :8080
docker run -d --name central --network agentids-net \
  -v ./tls:/tls:ro -v agentids-state:/var/lib/agentids \
  --entrypoint /agentids-central \
  agentids -config /config/central.yaml

# collector — reaches central by name over the network
docker run --rm --network agentids-net \
  -v ./tls:/tls:ro \
  -v /home:/home:ro \
  agentids -config /config/collector.yaml -once
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
