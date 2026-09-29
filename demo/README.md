# demo — see it work, in one command

```
./run.sh
```

No root. No live auditd. No Docker daemon. No network. Everything comes from the
captured fixtures in [`fixtures/`](fixtures/) and the configs beside them, driven
through the **real** collector binary, the **real** rules engine, and the
**real** central service.

## What it shows

The demo replays two captured streams — an auditd log and a `docker events`
log — that together encode a CARBONATO-class intrusion. You should see six rule
matches, two of them **critical**:

| Rule | Severity | What it caught |
|---|---|---|
| `carbonato.exec.shell_spawn` | medium | the agent started a shell |
| `carbonato.exec.ssh_key_heredoc` | high | it reached for an SSH key |
| `carbonato.exfil.egress_after_change` | high | it called out to a chat/C2 endpoint |
| `carbonato.net.listener_bound` | medium | it opened a listening socket (a foothold) |
| `carbonato.container.privileged_start` | **critical** | a **privileged, host-mounted container appeared** — the entry vector |
| `carbonato.container.host_root_mount` | **critical** | it mounted the host root / Docker socket |

Then a second pass edits the stand-in persona file, producing an
`identity.drift` (Layer 1), and re-runs the collector. Because the collector
feeds drift to the timeline *before* Layer 3 evaluates the actions, the
correlation rule `carbonato.container.after_identity_change` now fires — the
timed spine of the intrusion, and the thing this project was built for.

## Why it is built this way

- **Fixtures, not a live system.** A demo that needs root and a misconfigured
  Docker daemon to be reproduced is a demo nobody reproduces. These streams are
  static files; the run is deterministic.
- **A fake `docker` CLI** ([`fake-docker.sh`](fake-docker.sh)) stands in for the
  real one so the *daemon-side* container path — including `docker inspect`
  HostConfig enrichment — is exercised with no Docker installed. The collector
  spawns it exactly as it would spawn `docker`; it answers `events` and
  `inspect`. That is the whole point of the `Source` seam: the collector does not
  know the difference.
- **The real binaries.** Nothing here re-implements the pipeline; `run.sh`
  starts the actual `agentids-central` and `agentids-collector`.

## Files

| File | What it is |
|---|---|
| `run.sh` | the one-command driver: start central, two collector passes, print alerts |
| `fixtures/audit.log` | captured auditd stream: execve → connect → bind/listen |
| `fixtures/docker-events.jsonl` | captured `docker events`: a container start |
| `fake-docker.sh` | a `docker` CLI stand-in (`events` + `inspect`), no daemon |
| `collector.yaml` | collector config pointed at the fixtures and the fake CLI |
| `central.yaml` | central config: listen on :18080, log alerts |
| `agent-root/SOUL.md` | the stand-in persona file Layer 1 watches |

`run.sh` leaves central's full log in `out/central.log` and cleans up after
itself, including the `agent-root/SOUL.md` edit.
