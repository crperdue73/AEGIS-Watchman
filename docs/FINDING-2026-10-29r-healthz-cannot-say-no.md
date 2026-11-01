# FINDING 2026-10-29r — the operator's only green light is a literal, and the doc defends it with the right argument for the wrong probe

**Status:** Instrument added (`scripts/healthprobe.py`). Checklist-level defect; **the documented rationale is correct and I am not arguing against it.**
**Instrument:** `scripts/healthprobe.py` · **Beat:** 667

## The doc is right, and that is the first thing to say

`docs/deploy-central.md:48`:

> *"`/healthz` deliberately touches no disk: a health check that reads a store turns a slow disk into a failed health
> check, and a failed health check into a restart loop."*

**Correct, and standard practice.** A *liveness* probe that blocks on I/O is an outage amplifier: the component that
should have degraded slowly gets killed and respawned into the same bad disk. I am not proposing they change this.

## The argument is answering a different question

Liveness asks *"is this process wedged?"* Readiness asks *"can this process do its job?"* They are separate probes
because they have separate **consequences**: liveness failure ⟹ **restart**; readiness failure ⟹ **take it out of
rotation / flag it / leave it alone** — which is precisely what you want for a slow or broken disk.

Measured over the mux (http.go:42-53):

```
/healthz        -> `{"status":"ok"}`   hardcoded literal, request ignored
/api/v1/events  /api/v1/actions  /api/v1/fleet  /api/v1/agents  /agents  /
```

**No readiness endpoint. No dependency status. Nothing on the probe path that can return non-200 for any reason.**
`Service` holds four collaborators that can each be broken — `store`, `records`, `alerter`, `manifests` — and the one
endpoint a monitor polls can see none of them.

## What the probe cannot tell you

Credit first: **startup refuses an unopenable store** (`cmd/central/main.go:60-62`, `log.Fatalf`). "Store never opened"
is a crash, loudly, before traffic. I claim nothing against that.

The uncovered case is **the store breaking after a successful start** — disk fills, path unmounts, backend locks, file
deleted. From that moment:

- `records.Append` errors are logged and dropped, and `IngestActions` still answers **success** (measured, beat 666).
- `/api/v1/fleet` shows whatever an empty store looks like.
- `/healthz` says `{"status":"ok"}`. It said so before. It would say so with the disk physically removed.

And this is where it stops being theoretical: **the deployment's own sanity checklist**
(`docs/deploy-central.md:356`) is

```
- [ ] `curl -s localhost:8080/healthz` returns `{"status":"ok"}`.
```

**That check passes on a central that is storing nothing.** A compose `HEALTHCHECK` or a load-balancer probe built on it
reports healthy forever. A nil `records` store is even documented as *safe* ("Nil disables retention; alerts still fire")
— which is good design for an unconfigured deployment and is exactly why a *broken* one is indistinguishable from a
deliberately-absent one. This is beats 663 / 664 / 666 arriving at the operator's console: a real green light, checking
the wrong thing.

## The distinction, stated plainly

I am not claiming `/healthz` lies. It truthfully reports the only thing it claims: the process is up and serving. The
gap is that **"up" is the only question this deployment can answer**, and the checklist treats it as though it were
"working."

## Fix (Dad's call)

1. **Add `GET /readyz`** returning `200` when the dependencies it can cheaply probe are usable and **`503` naming the
   failing one** (records store append probe, manifest registry, alerter). Bounded, timeout-guarded — the restart-loop
   concern is handled by it *not* being a liveness probe.
2. **Leave `/healthz` exactly as it is** — a pure liveness literal is right for a liveness probe.
3. **Change the checklist** to poll `/readyz` for "can it store?" and keep `/healthz` for "is it up?".
4. Longer term (beat 666 fix 3): return the retain error to the collector, so `accepted` never means "discarded" — then
   readiness has something honest to report.

## Claim / not claimed

**Claimed:** `/healthz` writes a hardcoded literal, ignores the request, has no path to any non-200; the mux exposes no
readiness route; `Service` holds four nullable collaborators the probe never reads; the documented checklist step passes
with a broken store.

**Not claimed:** that `/healthz` should read the disk (it should not, for the documented and correct reason); that I
observed a production outage; or that readiness can probe *everything* cheaply — a readiness probe has its own
slow-dependency problem, which is why it must be bounded and timeout-guarded rather than absent. No server was started;
all of this is read from source.

## Mutation proof

| mutation | result |
|---|---|
| `/healthz` gains `if s.records == nil { WriteHeader(503) }` | **FAIL** ×2 ("touches s.records", "cannot emit non-200") · rc=1 |
| add `mux.HandleFunc("/readyz", ...)` (the proposed fix) | **FAIL**, "no readiness route exists" · rc=1 |
| control | 13 rows green · rc=0 |

Also fixed before trusting the green: one check written as `A and B or C` (precedence made it pass on `C` alone); split
into two independent assertions.

## Gate

**80 suites / 1597 checks / 0 failures.** Baseline rewritten (`healthprobe.py 12`).
