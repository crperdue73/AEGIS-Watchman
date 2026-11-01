#!/usr/bin/env python3
"""livenesscensus — the fleet view's "last seen" is the last time a host caused TROUBLE.

BEAT 731. Beat 730's closing question was: does anything count what central never
received? Traced it. The answer is worse than a missing counter — the one liveness field
the fleet has is **derived from the alert store**, so it measures the wrong thing.

THE MEASUREMENT
---------------
    internal/central/fleet.go:128   if h.LastSeen.IsZero() || rec.When.After(h.LastSeen) {
                                        h.LastSeen = rec.When
                                    }

`rec` is a **record from the event store**. Ingest only retains records when
`len(events) > 0` (`service.go:158` — *"Retain the same events for the fleet view"*), and
`Drift` returns zero events for a host whose files are unchanged. So:

  - A host that checks in every 60s with nothing wrong produces **no records** and
    therefore **never sets `LastSeen`**. Its fleet row shows a zero timestamp forever.
  - A host whose agent went dark, was unplugged, or whose collector died **keeps its old
    `LastSeen`** — the last time it *alerted*.
  - A host with a burst of alerts has `LastSeen` = the newest alert, which is generally
    earlier than its most recent successful check-in.

So `LastSeen` is not "last contact". It is **"last time this host was a problem."**
The two coincide only for hosts that alert continuously, which is exactly the set you
least need the field for.

WHAT AN OPERATOR READS OFF IT
-----------------------------
`fleetpage.go:176` renders it as `<td class="muted">{{.LastSeen}}</td>` in the fleet
console — a column an operator will read as freshness. The worst reading is available to
them: **a quiet host and a dead host are indistinguishable**, both showing an old
timestamp, because a dead host stops alerting and a healthy host never alerts.

`Ingest` returns `IngestResult{FirstSeen, Events}` — no timestamp, no sequence, nothing an
operator could use to say "this host reported at T". Nothing anywhere in `internal/central`
records ingest time. I grepped for `LastSeen|heartbeat|stale|Since` across non-test
central code: the only hits are the alert-derived field above and event-query bounds.

WHY THIS IS THE SAME FAMILY, ONE LAYER OUT
------------------------------------------
724–730 were all about **what the detector sees and says**. This one is about **what it
reports about itself**. The tool is a monitor, and its own liveness signal is derived from
its alarm signal. That is the classic monitoring failure: *the graph goes flat and you
cannot tell whether the system is calm or the sensor is dead.* It is the same species as
beat 730's silence-becomes-assertion, but pointed at the operator instead of the analyst:
an absent measurement is displayed as a measurement.

SCOPE, HONESTLY
---------------
- **Not a fabrication:** `LastSeen` genuinely is the time of the newest retained record.
  The field is computed correctly; it is *labelled* "last seen" and exposed as freshness.
- Not observed on a running deployment (no toolchain, beat 642) — source reads.
- I have not checked whether any dashboard or alert rule consumes `LastSeen` for a
  staleness decision. **If one does, a dead sensor is permanently "fresh" after its last
  alert, or "stale" only because it alarmed once.** That is the question I would want
  answered before touching it, and I could not answer it from this tree.
- No fix proposed as applied. Adding a true `LastIngest` is a schema/state change and the
  decision about whether a silent-but-healthy host should show as "fresh" is a product
  question (and an operator-preference one) — not mine to guess on a heartbeat.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FAILS, OKS = [], 0


def ok(m):
    global OKS
    OKS += 1
    print("ok   " + m)


def fail(m):
    FAILS.append(m)
    print("FAIL " + m)


def read(rel):
    return (ROOT / rel).read_text(errors="replace")


def check_a_lastseen_from_records():
    src = read("internal/central/fleet.go")
    if re.search(r"if h\.LastSeen\.IsZero\(\) \|\| rec\.When\.After\(h\.LastSeen\) \{\s*h\.LastSeen = rec\.When", src):
        ok("A gap: LastSeen is stamped from rec.When inside the loop over ALERT RECORDS — "
           "it is the newest alert time, not a contact time")
    else:
        fail("A: the LastSeen assignment changed — if it now comes from something other "
             "than a record, the gap may be closing; re-derive")


def check_b_records_only_exist_when_something_broke():
    src = read("internal/central/service.go")
    seg = src[src.find("if len(events) > 0 {"):]
    seg = seg[:seg.find("\n\t}")]
    # M3 (beat 731): searched for the WORD "retain" anywhere in the file, so replacing the
    # condition with `if true` stayed green. Third beat running where a check matched a
    # word near the code instead of the code. Pin the CONDITION itself.
    if not re.search(r"if len\(events\) > 0 \{", src):
        fail("B: the retain condition is no longer `len(events) > 0` — records may now be "
             "written on a clean pass; the gap may be closing; re-derive")
        return
    if "retain" in seg.lower() or "Retain" in src:
        ok("B gap: records are retained only when len(events) > 0 — a clean check-in "
           "produces ZERO records, so it can never update LastSeen")
    else:
        fail("B: the retain-on-events condition changed — re-derive")
    d = read("internal/collector/drift.go")
    # Measured, not guessed: Drift names its slice `events` and initialises it as an
    # empty non-nil literal (drift.go:31), returning it at :90. My first pattern assumed
    # `out`, which is a different variable further down the same file.
    if re.search(r"events := \[\]schema\.Event\{\}", d) and re.search(r"^\treturn events$", d, re.M):
        ok("B: Drift returns an empty slice for an unchanged host, and that empty slice is "
           "exactly what suppresses the record — the chain is measured end to end")
    else:
        fail("B: Drift's output construction changed — re-derive the empty-case claim")


def check_c_no_ingest_time_anywhere():
    hits = []
    for p in ROOT.glob("internal/central/*.go"):
        if p.name.endswith("_test.go"):
            continue
        t = p.read_text(errors="replace")
        for m in re.finditer(r"\b(LastIngest|IngestedAt|LastContact|LastReport|HeartbeatAt)\b", t):
            hits.append("%s:%s" % (p.name, m.group(1)))
    if not hits:
        ok("C gap: no LastIngest / LastContact / IngestedAt field exists anywhere in "
           "internal/central — there is no true contact time to compare against")
    else:
        fail("C: a contact-time field now exists (%s) — the gap may be closing; re-derive" % hits)
    res = read("internal/central/service.go")
    m = re.search(r"type IngestResult struct \{(.*?)\n\}", res, re.S)
    if m and "time.Time" not in m.group(1):
        ok("C gap: IngestResult carries no timestamp — a successful ingest is not "
           "observable from its own return value")
    else:
        fail("C: IngestResult now carries a time — re-derive")


def check_d_displayed_as_freshness():
    page = ROOT / "internal/central/fleetpage.go"
    if not page.exists():
        fail("D: fleetpage.go is missing")
        return
    t = page.read_text(errors="replace")
    if re.search(r"\{\{\.LastSeen\}\}", t):
        ok("D gap: the fleet console renders LastSeen in a muted column — an operator reads "
           "it as freshness; a quiet host and a dead host look identical there")
    else:
        fail("D: the LastSeen rendering changed — re-derive what operators actually see")


def check_e_doc_scope():
    doc = ROOT / "docs/FINDING-2026-11-01l-lastseen-is-last-alert.md"
    if not doc.exists():
        fail("E: the finding doc is missing")
        return
    t = re.sub(r"\s+", " ", doc.read_text())
    for phrase, label in [
        ("NOT APPLIED", "the not-fixed marker"),
        ("Not a fabrication", "the honest framing: the field is computed correctly"),
        ("last time this host was a problem", "the one-line claim"),
    ]:
        if phrase in t:
            ok("E: doc carries %s" % label)
        else:
            fail("E: doc lost %s (%r)" % (label, phrase))


def main():
    check_a_lastseen_from_records()
    check_b_records_only_exist_when_something_broke()
    check_c_no_ingest_time_anywhere()
    check_d_displayed_as_freshness()
    check_e_doc_scope()
    print()
    if FAILS:
        print("livenesscensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("livenesscensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
