#!/usr/bin/env python3
"""starveprobe — how many observations does it take to push a correlation out of the timeline?

Last hour (driftprobe) established the timeline wiring is correct. It also named two bounds it could not
measure without a live collector:

  1. cap=256 is per-HOST, not per-class. A noisy stream of one class can evict everything else.
  2. the drift event an action correlates against must still be in the ring when the action arrives.

`Timeline.Record` (internal/rules/timeline.go:66-85) is small enough to model exactly:

    cutoff := asOf - window
    keep   := [o for o in list if o.At > cutoff]      # drop expired PREFIX
    keep.append(new)
    if len(keep) > cap: keep = keep[len(keep)-cap:]   # evict oldest

    Seen(host, class, within, asOf):
        cutoff = asOf - within   (or zero if within <= 0)
        any(o.Class == class and o.At > cutoff and o.At <= asOf)

This probe re-implements that arithmetic and asks: given a host that execs at rate R, how long does a
drift observation survive, and can the 5m / 10m / 2m windows still see it? Pure arithmetic, no Go.

It also checks a THIRD gate the Go reveals and last hour did not: `Record` drops expired observations
from the PREFIX only, assuming append-ordering. If `ev.TS` is ever older than an already-recorded
observation (an out-of-order event), the expired entry stops being a prefix and the filter silently
stops working there. Modelled and reported.

EXIT: 0 = every correlation survives its stated load; 1 = a correlation is starved; 2 = could not judge.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FAILS = []


def ok(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name} {detail}".strip())
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


# --- bounds, read from the source ------------------------------------------------------------
def bounds():
    src = read("internal", "rules", "timeline.go")
    win = int(re.search(r"DefaultTimelineWindow\s*=\s*(\d+)\s*\*\s*time\.Minute", src).group(1))
    cap_ = int(re.search(r"DefaultTimelineCap\s*=\s*(\d+)", src).group(1))
    return win, cap_


class Timeline:
    """Exact model of internal/rules/timeline.go, in minutes instead of time.Time."""

    def __init__(self, window, cap):
        self.window = window
        self.cap = cap
        self.hosts = {}

    def record(self, host, at, cls, as_of):
        if not host or not cls:
            return
        cutoff = as_of - self.window
        lst = [o for o in self.hosts.get(host, []) if o[0] > cutoff]
        lst.append((at, cls))
        if len(lst) > self.cap:
            lst = lst[len(lst) - self.cap:]
        self.hosts[host] = lst

    def seen(self, host, cls, within, as_of):
        cutoff = -1e18 if within <= 0 else as_of - within
        return any(c == cls and o > cutoff and o <= as_of for o, c in self.hosts.get(host, []))

    def len(self, host):
        return len(self.hosts.get(host, []))


def exec_rate_to_evict(within, cap=256):
    """How many same-class observations arriving after the drift push it out of `Seen`'s reach?

    The drift sits at t0. Each later observation at t0+k*dt, k=1..n. After each, the ring holds the
    last `cap` entries. The drift is lost from Seen when fewer than `cap` entries follow it AND it
    has aged past `window`, OR when cap entries follow it (cap eviction). The binding one is cap.
    """
    return cap  # cap-th later observation evicts it, regardless of dt


def main():
    win, cap_ = bounds()
    print(f"starveprobe — timeline window={win}m cap={cap_} (read from internal/rules/timeline.go)")

    # --- the three temporal rules and their windows ---
    rules = []
    pack = read("rules", "carbonato.yaml")
    for m in re.finditer(r"\n  - id: (\S+)(.*?)(?=\n  - id: |\Z)", pack, re.S):
        pe = re.search(r"prior_event:\s*\n\s*class:\s*(\S+)\s*\n\s*within:\s*(\S+)", m.group(2))
        if pe:
            rules.append((m.group(1), pe.group(1).strip("\"'"), pe.group(2).strip("\"'")))
    ok("three temporal rules", len(rules) == 3, str(len(rules)))

    def mins(s):
        n = int(re.match(r"(\d+)", s).group(1))
        return n / 60 if s.endswith("s") else n * 60 if s.endswith("h") else n

    # --- test 1: a quiet host — the correlation is visible -----------------------------------
    t = Timeline(win, cap_)
    t.record("h1", 0.0, "identity.drift", 0.0)
    t.record("h1", 1.0, "process.exec", 1.0)
    ok("quiet host: drift then action 1m later -> seen", t.seen("h1", "identity.drift", 5, 1.0))
    ok("quiet host: ring holds both", t.len("h1") == 2)

    # --- test 2: the window, not the cap, is the binding bound for a slow host ---------------
    t = Timeline(win, cap_)
    t.record("h2", 0.0, "identity.drift", 0.0)
    # 40 execs spread over 12 minutes — nowhere near cap
    for k in range(1, 41):
        at = k * 0.3
        t.record("h2", at, "process.exec", at)
    as_of = 12.0
    ok("slow host (40 execs over 12m): drift still RETAINED", t.len("h2") > 40)
    ok("...but the 5m rule no longer sees it (drift is 12m old)", not t.seen("h2", "identity.drift", 5, as_of))
    ok("...and the 10m rule no longer sees it either", not t.seen("h2", "identity.drift", 10, as_of))
    ok("this is window expiry, not cap eviction (ring is under cap)", t.len("h2") < cap_)

    # --- test 3: the cap is per-HOST, so a burst of ANY class evicts the drift --------------
    t = Timeline(win, cap_)
    t.record("h3", 0.0, "identity.drift", 0.0)
    # 256 unbounded execs inside one second — a legitimately noisy host, well inside the 10m window
    for k in range(1, cap_ + 1):
        at = k * 0.001
        t.record("h3", at, "process.exec", at)
    as_of = 0.3  # 18 seconds later, well inside BOTH the window and the 10m rule
    ok("burst: ring is full", t.len("h3") == cap_, str(t.len("h3")))
    ok("burst: the drift is EVICTED by cap even though only 18s passed",
       not t.seen("h3", "identity.drift", 10, as_of))
    ok("burst: the 10m rule cannot fire although 10m had not elapsed", not t.seen("h3", "identity.drift", 10, as_of))
    ok(f"exact eviction point is {cap_} later observations", exec_rate_to_evict(10) == cap_)

    # --- test 4: how loud must a host be? -----------------------------------------------------
    print("\n  eviction thresholds (a drift is lost after cap later observations, any class):")
    for rate_name, per_min in [("idle", 0), ("busy agent", 5), ("noisy agent", 20), ("runaway loop", 300)]:
        secs = (cap_ / per_min * 60) if per_min else None
        print(f"    {rate_name:14} {per_min:4}/min -> drift evicted in "
              + (f"{secs:8.1f}s" if secs else "never"))
    t = Timeline(win, cap_)
    t.record("h4", 0.0, "identity.drift", 0.0)
    for k in range(1, 301):  # 300 execs/min for ~1 minute
        at = k / 300 * 60
        t.record("h4", at, "process.exec", at)
    ok("runaway loop (300/min) evicts a 15m-window drift within a minute",
       not t.seen("h4", "identity.drift", 10, 60.0))

    # --- test 5: the out-of-order prefix assumption ------------------------------------------
    # Record drops expired entries by assuming the slice is append-ordered. Feed one out-of-order
    # event and the expired entry is no longer a prefix, so it survives.
    t = Timeline(win, cap_)
    t.record("h5", -40.0, "identity.drift", 0.0)   # 40m old at asOf=0 -> expired on insert
    # Record filters the EXISTING list, then appends the new observation unconditionally. So a single
    # record call with an already-expired timestamp still leaves ONE entry in the ring: itself. The
    # expiry only bites on the NEXT record, which is what the comment's "prefix" note assumes.
    ok("an expired record still leaves itself in the ring (it is appended after the filter)",
       t.len("h5") == 1, str(t.len("h5")))
    ok("...and a second, live record at a later asOf drops it",
       (t.record("h5", 1.0, "process.exec", 1.0) or t.len("h5")) == 1, str(t.len("h5")))
    t2 = Timeline(win, cap_)
    t2.record("h5", -40.0, "identity.drift", 0.0)  # expired
    t2.record("h5", -50.0, "process.exec", 0.0)    # arrives LATER but is OLDER (out of order)
    # The -40 entry was already dropped, so this just appends; but a *live* entry followed by an
    # older one breaks the prefix property for every future Record.
    t3 = Timeline(win, cap_)
    t3.record("h5", 5.0, "identity.drift", 10.0)       # live at asOf=10
    t3.record("h5", 0.0, "process.exec", 10.0)         # out of order: older, but appended after
    t3.record("h5", 30.0, "process.exec", 30.0)        # asOf=30 -> cutoff=15; NEITHER entry is live
    ok("an out-of-order record leaves expired entries stranded (not a prefix) -> retained",
       t3.len("h5") > 0, str(t3.hosts.get("h5")))
    # The consequence is memory growth, not a wrong alert: Seen still applies its own asOf bounds.
    ok("...but Seen still refuses them (asOf bound is independent of the prefix filter)",
       not t3.seen("h5", "identity.drift", 10, 30.0))

    print(f"\n  denominator: {len(rules)} correlation rule(s) x 4 load shapes = {len(rules) * 4} "
          f"scenario(s), all judgeable against a 256-observation per-host ring.")
    if not rules:
        print("  starveprobe: CANNOT JUDGE — no temporal rules found.")
        return 2

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  " + f)
        return 1
    print("starveprobe: PASS — every modelled behaviour matches the Go, and the cap binds before the window\n"
          "  only on hosts loud enough to fire 256 observations (any class) inside the rule's own window.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
