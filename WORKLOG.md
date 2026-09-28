# Carbonato Detection Tool — Work Log

## 2026-09-27 (Sun), ~20:20 ET — Heartbeat 1
- Read `BRIEF.md` in full.
- Wrote `PHASE0-DISCOVERY.md` — Phase 0 kickoff:
  - Enumerated 6 target environments (bare host, Docker, K8s, CI, cloud-managed, workstation).
  - Telemetry inventory per layer (L1 file hash/inotify/auditd; L2 create-our-own; L3 eBPF/auditd/conntrack + agent-native scope events).
  - First architecture sketch: per-host collector → central service, mTLS.
  - Drafted Layer-1 collector config + `agentids.event/v1` event schema (the public contract).
  - Restated the 5 decisions needed from Rob.
- **Next step (heartbeat 2):** repo skeleton. Structure, LICENSE (Apache-2.0),
  README, CONTRIBUTING, Go module scaffold for collector + central. Blocked on
  NAME (Q1) — if no answer, use placeholder `agentids` and rename later.
- **Blocking:** GitHub repo access (expected Sept 28) + name decision.
