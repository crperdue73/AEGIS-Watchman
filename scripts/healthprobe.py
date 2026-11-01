#!/usr/bin/env python3
"""healthprobe — /healthz cannot fail, and the doc defends it with the wrong argument.

THE DOC IS RIGHT ABOUT ONE THING, AND IT MATTERS
------------------------------------------------
docs/deploy-central.md:35 and :48 say:

    "/healthz deliberately touches no disk: a health check that reads a store turns
     a slow disk into a failed health check, and a failed health check into a
     restart loop."

**That is correct and well-understood.** A *liveness* probe that blocks on I/O is a
classic outage amplifier: the thing that should have degraded instead gets killed and
respawned into the same bad disk. I am not arguing against this sentence.

THE ARGUMENT IS BEING USED TO ANSWER A DIFFERENT QUESTION
---------------------------------------------------------
Liveness answers "is this process wedged?" Readiness answers "can this process do its
job?" They are different probes because they have different *consequences*: a failed
liveness probe gets you RESTARTED; a failed readiness probe gets you taken out of
rotation, or flagged, or left alone -- which is exactly what you want for a slow disk.

This deployment exposes only the first. Measured over the mux (http.go:42-53):

    /healthz         -> `{"status":"ok"}` hardcoded literal, request ignored
    /api/v1/events   /api/v1/actions   /api/v1/fleet   /api/v1/agents
    /agents          /                (pages)

No readiness endpoint, no dependency status, nothing on the probe path that can
return non-200 for any reason whatsoever. `Service` holds a `store`, a `records`
RecordStore, an `alerter`, and a `manifests` registry -- four collaborators that can
each be broken -- and the one endpoint a monitor polls cannot see any of them.

WHAT THE PROBE THEREFORE CANNOT TELL YOU (measured at startup, read from the code)
---------------------------------------------------------------------------------
cmd/central/main.go:60-62 DOES fail loudly if the store will not OPEN:

    if rs, err := cfg.RecordStoreFn(); err != nil { log.Fatalf(...) }

So "store never opened" is a startup crash -- good, and I claim nothing against it.
The uncovered case is **the store breaking AFTER a successful start**: disk fills,
path unmounted, backend locked, file deleted. After that moment:

  - `records.Append` errors are logged and dropped (beat 666 measured: `retain` logs
    and returns nothing; `IngestActions` still answers success).
  - `/api/v1/fleet` has nothing to report, and what it does report it reports as
    whatever an empty store looks like.
  - `/healthz` says `{"status":"ok"}` -- it did before, it does now, it would say so
    with the disk removed.

**The deployment's own sanity checklist (docs/deploy-central.md:356) is
`curl -s localhost:8080/healthz` returns `{"status":"ok"}`.** That check passes on a
central that is storing nothing. A compose HEALTHCHECK built on it will report
healthy forever. This is beats 663/664/666 arriving at the operator's console: the
green light is real, and it is checking the wrong thing.

CLAIM / NOT CLAIMED
-------------------
CLAIMED: /healthz writes a hardcoded literal and cannot return non-200; the mux
    exposes no readiness/dependency endpoint; Service holds 4 nullable collaborators
    none of which the probe path reads; the documented checklist step passes with a
    broken store.
NOT CLAIMED: that /healthz should read the disk -- it should NOT, for the documented
    and correct reason. NOT CLAIMED: that I observed a production outage. No server
    was started; this is read from source. The distinction I am drawing is
    liveness vs readiness, and the fix keeps liveness exactly as it is.
"""

import sys
import pathlib
import re


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    fails = 0

    def check(name, ok):
        nonlocal fails
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            fails += 1

    try:
        http = (root / "internal/central/http.go").read_text()
        svc = (root / "internal/central/service.go").read_text()
        main_go = (root / "cmd/central/main.go").read_text()
        doc = (root / "docs/deploy-central.md").read_text()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read input ({e}); a missing input is not a pass")
        return 1

    print("-- the liveness probe cannot fail --")
    hz = http.split('mux.HandleFunc("/healthz"')[1].split("mux.HandleFunc")[0]
    check("/healthz body is a hardcoded ok literal", '{"status":"ok"}' in hz)
    check("/healthz ignores the request (no read of r)", "_ *http.Request" in hz)
    check("/healthz never touches s.records / store / alerter",
          not any(k in hz for k in ("s.records", "s.store", "s.alerter", "s.manifests")))
    # property, not shape: no non-200 status can be emitted on this path
    check("/healthz cannot emit any non-200 status",
          "WriteHeader" not in hz and "StatusInternalServerError" not in hz
          and "StatusServiceUnavailable" not in hz)

    print("-- and there is no readiness endpoint to make up for it --")
    routes = re.findall(r'HandleFunc\("([^"]+)"', http)
    print(f"     routes: {routes}")
    check("no readiness / dependency / readyz route exists",
          not any(r.strip("/") in ("ready", "readyz", "readiness", "health/ready",
                                   "status", "deps", "dependencies") for r in routes))

    print("-- four collaborators the probe cannot see --")
    sb = svc.split("type Service struct {")[1].split("\n}")[0]
    collabs = [f for f in ("store", "records", "alerter", "manifests")
               if re.search(rf"^\t{f}\s", sb, re.M)]
    check(f"Service holds all four nullable collaborators {collabs}", len(collabs) == 4)
    # Property: nil is documented as SAFE for these fields. That is what makes a
    # broken collaborator indistinguishable from an unconfigured one -- so it is the
    # claim the finding rests on, and it is checked as its own assertion.
    nil_safe = sum(1 for ln in sb.splitlines()
                   if re.match(r"\s*//.*\bNil (is safe|disables)", ln))
    check(f"at least one collaborator documents nil as safe/disabled ({nil_safe} such comments)",
          nil_safe >= 1)

    print("-- startup DOES refuse an unopenable store (recorded as good work) --")
    check("main.go log.Fatalf on store-open error",
          "cfg.RecordStoreFn()" in main_go and "log.Fatalf" in
          main_go.split("cfg.RecordStoreFn()")[1].split("}")[0])

    print("-- the documented sanity check passes on a broken store --")
    check("checklist tells the operator to accept a literal ok from /healthz",
          'curl -s localhost:8080/healthz` returns `{"status":"ok"}`' in doc)
    check("the doc states the deliberate no-disk rationale (which is correct)",
          "deliberately touches no disk" in doc)

    if fails:
        print(f"\nHEALTH PROBE RED: {fails}")
        return 1

    print("\n=> Liveness is right and should stay exactly as it is. What is missing is")
    print("   the OTHER probe: a readiness/dependency endpoint that CAN say no, which is")
    print("   what a monitor should alarm on. Without it, a central whose store broke")
    print("   after startup passes its own documented checklist forever -- liveness and")
    print("   capability reported by the same green light.")
    print("   Fix, Dad's call: add GET /readyz returning 503 with the failing dependency")
    print("   (records append probe, manifests, alerter) and leave /healthz untouched;")
    print("   update the checklist to poll /readyz for 'can it store?'. Do NOT make")
    print("   /healthz read the disk -- the doc is right about restart loops.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
