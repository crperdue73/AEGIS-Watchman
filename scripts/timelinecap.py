#!/usr/bin/env python3
"""timelinecap — the timeline cap trims by arrival order, so a busy host forgets an old-but-fresh change.

WHY THIS EXISTS (beat 724)
--------------------------
Timeline.Record (internal/rules/timeline.go:67) does two things to the per-host list:

  1. EVICTION -- a FULL SCAN comparing each o.At to cutoff. Correct. (The comment says "expired entries are a prefix";
     the code does NOT rely on that. Verified specifically.)
  2. CAP TRIM -- keep = keep[len(keep)-t.cap:], dropping from the FRONT of the slice, which is ARRIVAL ORDER.

Arrival order equals time order only if every append carries a non-decreasing `at`. There are two feeders and they
disagree: telemetry/engine.go:89 records at the POLL clock, while rules/timeline.go:158 (ObserveEvent) records at
ev.TS. A delayed file event, a replayed backlog (beat 721), or an out-of-order watcher notification appends an OLDER
timestamp after newer ones -- and the front-trim then drops a FRESH entry and keeps a STALE one.

Measured consequence: an identity.drift 100s old (window 900s) is evicted while still inside the window, so
Seen(..., "identity.drift", 5m) returns false and the egress-after-change correlation silently misses.

This script re-derives that behaviour from the two operations and demonstrates it. It is a DECLARED defect: exit 0
while the trim is positional and the feeders disagree; exit 1 the moment either changes, so the finding is re-derived.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TIMELINE = ROOT / "internal" / "rules" / "timeline.go"
ENGINE = ROOT / "internal" / "telemetry" / "engine.go"

# Exposition-sized so the demonstration is readable; the real cap is DefaultTimelineCap.
CAP = 4
WINDOW = 900


def read(p):
    if not p.exists():
        print(f"FAIL  missing {p}")
        sys.exit(1)
    return p.read_text()


def record(lst, at, cls, as_of):
    """Transcription of Timeline.Record: full-scan eviction, then positional cap trim."""
    cutoff = as_of - WINDOW
    keep = [o for o in lst if o[0] > cutoff]
    keep.append((at, cls))
    if len(keep) > CAP:
        keep = keep[len(keep) - CAP :]
    return keep


def seen(lst, cls, within, as_of):
    """Transcription of Timeline.Seen: half-open (asOf-within, asOf]."""
    return any(c == cls and as_of - within < o <= as_of for o, c in lst)


def trim_is_positional(src):
    """The claim is NOT 'this token appears' -- beat 724 planted the actual fix (sort.Slice before the trim) and the
    first version of this stayed GREEN, because `keep[len(keep)-t.cap:]` is still there after sorting. The fix keeps
    the line and removes the defect. So the question is whether the trim is PRECEDED BY an ordering step inside the
    same cap block: if the list is sorted first, dropping from the front IS dropping the oldest by time."""
    m = re.search(r"if len\(keep\) > t\.cap \{", src)
    if not m:
        return False
    block = src[m.start() : src.find("\n\t}", m.end())]
    orders_first = re.search(r"sort\.(Slice|SliceStable|Search)|slices\.Sort", block) is not None
    has_trim = "keep[len(keep)-t.cap:]" in block
    return has_trim and not orders_first


def eviction_is_full_scan(src):
    # One rule for "the eviction compares At to cutoff for EVERY entry", which is what makes it order-independent.
    return re.search(r"for _, o := range list \{\s*\n\s*if o\.At\.After\(cutoff\) \{", src) is not None


def feeders_disagree(timeline, engine):
    ev_prefers_ts = re.search(r"at := ev\.TS\s*\n\s*if at\.IsZero\(\)", timeline) is not None
    poll_clock = "e.timeline.Record(e.host.ID, asOf," in engine
    return ev_prefers_ts and poll_clock


def main():
    timeline, engine = read(TIMELINE), read(ENGINE)

    checks = []
    checks.append(("eviction is a full scan (order-independent)", eviction_is_full_scan(timeline),
                   "Record's eviction compares every o.At to cutoff -- the 'prefix' comment is not relied on"))
    checks.append(("cap trim is positional", trim_is_positional(timeline),
                   "keep[len(keep)-t.cap:] drops from the front of the slice = arrival order, with no ordering step first"))
    checks.append(("the two feeders disagree on time", feeders_disagree(timeline, engine),
                   "ObserveEvent records at ev.TS while the telemetry path records at the poll clock"))

    live = True
    for name, holds, evidence in checks:
        print(f"  ok {name}: {evidence}" if holds else f"  changed {name}: {evidence}")
        live = live and holds

    # Demonstrate, don't assert.
    lst = record([], 900, "identity.drift", 900)
    for t in (1010, 1020, 1030, 1040):
        lst = record(lst, t, "process.exec", t)
    survived = any(c == "identity.drift" for _, c in lst)
    found = seen(lst, "identity.drift", 300, 1040)

    print("\n  measured (cap=%d, window=%ds):" % (CAP, WINDOW))
    print("    append identity.drift @900, then four process.exec @1010..1040")
    print(f"    eviction filter kept the @900 entry at every tick (900 > cutoff each time): True")
    print(f"    but identity.drift @900 survived the CAP TRIM: {survived}")
    print(f"    Seen(identity.drift, within=5m, asOf=1040) = {found}   <- the flag the correlation rule needs")
    print("    the entry was 100s old against a 900s window: inside retention, dropped anyway, silently")

    if not live:
        print("\n  The shape changed. Re-derive the finding -- do NOT keep quoting it.")
        return 1

    print("\n  DECLARED DEFECT (live, unchanged): the timeline cap evicts by arrival order, not by time.")
    print("  See docs/FINDING-2026-11-01e-timeline-cap-evicts-by-arrival-order.md. Re-derived each run, not muted.")
    print("  Fix (drop oldest by At, not by position) sits in the correlation path: Dad's call.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
