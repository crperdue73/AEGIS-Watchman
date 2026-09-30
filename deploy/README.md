# deploy — running agentids on a real host

Everything in this directory is for the operator who has tried the
[quickstart](../docs/quickstart.md) and now wants the tool *running*, not just
*running once*.

| Artifact | What it is |
|---|---|
| `agentids-collector.service` | systemd unit template for the per-host collector (Layer 1/2/3). |
| `agentids-central.service` | systemd unit template for the central service (ingest, alerting, fleet view). |
| `gen-certs.sh` | Generates the mutual-TLS material for the collector → central hop. |
| `docker/Dockerfile` | Builds the collector + central into one small image instead of installing units. |

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

`docker/Dockerfile` builds both binaries into a distroless image. It is the
lighter path when you already deploy containers; the systemd units are the
lighter path on a plain Linux host. Either way the configs and the rule packs
must be mounted in (`/etc/agentids` and `rules/`).
