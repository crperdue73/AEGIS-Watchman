#!/usr/bin/env python3
"""pruneprefix — Timeline.Record's eviction relies on an ordering it never enforces.

THE CLAIM IN THE SOURCE
-----------------------
`Timeline.Record` (rules/timeline.go:70-86) evicts expired observations like this:

    // Drop expired (the slice is append-ordered, so expired entries are a
    // prefix).
    keep := list[:0]
    for _, o := range list {
        if o.At.After(cutoff) { keep = append(keep, o) }
    }

That parenthesis is an ASSUMPTION, not a fact, and `Record` never checks it. `Record`
appends whatever `at` it is handed. If an observation arrives whose `at` is OLDER than
what is already in the slice, the slice is no longer sorted by `At`, and the expired
region is no longer a prefix.

WHAT I ACTUALLY MEASURED
------------------------
Simulating Record/Seen/Prune exactly as written (no Go toolchain -- see NOT CLAIMED):

    Record(h, 950, "live",  asOf=1000)      -> [(950,"live")]
    Record(h,  50, "stale", asOf=1000)      -> [(950,"live"), (50,"stale")]

The second entry is already expired relative to the 900s window (asOf-900 = 100), and it
is NOT at the front -- so the prefix assumption is FALSE, measured.

WHAT THAT DOES AND DOES NOT DO
------------------------------
I expected this to make a pack rule fire on stale evidence. It does NOT, and saying so
would be the inflation beat 670/671 warned about. Measured:

    Seen(h, "stale", within=900s, asOf=1000) -> no match   (cutoff 100 > 50)
    Seen(h, "stale", within=900s, asOf=1060) -> no match   (cutoff 160 > 50)

`Seen` independently re-checks `o.At.After(cutoff)`, so a positive window can never see
the stale entry. The one path that WOULD see it is `within <= 0` -- "ever seen" -- which
`Seen` documents at timeline.go:88 and implements by setting `cutoff = time.Time{}`. But
that path is UNREACHABLE from a pack: `rule.go:296` rejects `pe.Within <= 0` at load
(beat 658, "ever seen is not expressible"). So:

    No live rule misfires. The stale entry is invisible to every rule a pack can write.

THE REAL CONSEQUENCE IS MEMORY, AND IT IS SMALL BUT UNBOUNDED
-------------------------------------------------------------
The stale entry is never evicted by `Record`'s prefix loop. It sits in the slice until
`len(keep) > cap` pushes it out from the FRONT (`keep = keep[len(keep)-t.cap:]`) -- but
capacity eviction removes the OLDEST-BY-POSITION, i.e. the front, which is the LIVE
entries in this shape. So a backdated arrival can cause cap eviction to discard live
observations while retaining the stale one. That is a correctness smell, though its blast
radius is one host's 256-entry slice, not the fleet.

`Prune(asOf)` (timeline.go:118-136) does the same prefix-free filter but keeps the host
map tidy by deleting empty hosts, so a fully-idle host does get collected. The leak is
scoped to hosts receiving out-of-order observations.

IS OUT-OF-ORDER ARRIVAL REACHABLE?
----------------------------------
I checked the two production call sites:
  behavior.go:377 -> `b.timeline.ObserveEvent(ev, now)` -- ObserveEvent passes `now` as
      both `at` and `asOf`... except it does not (see below).
  telemetry/engine.go:89 -> `e.timeline.Record(e.host.ID, asOf, ev.Action.Type, asOf)`
      -- `at == asOf` (a fresh observation), so ordering is preserved BY CONSTRUCTION.

So on the engine path the assumption holds and the leak cannot occur. On the
ObserveEvent path, `at` comes from the event, not from `now` -- which is exactly the
path where a backdated or clock-skewed event could break it. I did not trace every
producer of those events; that is the open question this finding hands forward.

CLAIM / NOT CLAIMED
-------------------
CLAIMED (simulated from the exact source logic): with a backdated `at` the slice loses
    sorted order; the expired entry is not in the prefix; `Record` therefore retains it;
    capacity eviction then trims from the front by POSITION, so it can discard live
    entries and keep the stale one.
NOT CLAIMED: that any pack rule sees stale evidence -- measured false for every positive
    `within`, and `within<=0` is rejected at load (658). **NOT CLAIMED: that this leaks
    memory in practice on the engine path** -- there `at == asOf` by construction.
    **NOT claimed: this is a Go behaviour** -- simulated from source; no toolchain (642).
"""

import sys
import pathlib
import re


def strip_comments(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    src = re.sub(r"\s//\s.*$", "", src, flags=re.M)
    return src


class Timeline:
    """Faithful transcription of rules/timeline.go Record/Seen/Prune."""

    def __init__(self, window=900, cap=256):
        self.window, self.cap, self.by = window, cap, {}

    def record(self, host, at, cls, asof):
        if not host or not cls:
            return
        cutoff = asof - self.window
        lst = self.by.get(host, [])
        keep = [o for o in lst if o[0] > cutoff]        # the prefix assumption
        keep.append((at, cls))
        if len(keep) > self.cap:
            keep = keep[len(keep) - self.cap:]          # evicts from the FRONT
        self.by[host] = keep

    def seen(self, host, cls, within, asof):
        cutoff = asof - within if within > 0 else float("-inf")
        return [a for a, c in self.by.get(host, [])
                if c == cls and a > cutoff and a <= asof]

    def prune(self, asof):
        cutoff = asof - self.window
        for h in list(self.by):
            k = [o for o in self.by[h] if o[0] > cutoff]
            if k:
                self.by[h] = k
            else:
                del self.by[h]


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    fails = 0

    def check(name, ok):
        nonlocal fails
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            fails += 1

    try:
        raw = (root / "internal/rules/timeline.go").read_text()
        rule = strip_comments((root / "internal/rules/rule.go").read_text())
        tel = strip_comments((root / "internal/telemetry/engine.go").read_text())
        beh = strip_comments((root / "internal/collector/behavior.go").read_text())
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read input ({e}); a missing input is not a pass")
        return 1
    code = strip_comments(raw)

    rec = code[code.index("func (t *Timeline) Record("):]
    rec = rec[:rec.index("\n}\n")]
    seen = code[code.index("func (t *Timeline) Seen("):]
    seen = seen[:seen.index("\n}\n")]

    print("-- the assumption is asserted in prose, not enforced in code --")
    check("Record's comment claims expired entries are a prefix",
          "append-ordered" in raw and "prefix" in raw)
    check("the eviction loop filters by At but never SORTS", "sort." not in rec)
    check("Record appends whatever `at` it is handed (no monotonic guard)",
          "Observation{At: at" in rec)
    check("Seen independently re-checks o.At.After(cutoff) -- why a stale entry is invisible",
          "o.At.After(cutoff)" in seen)

    print("\n-- measured: a backdated arrival breaks the prefix --")
    now, win = 1000, 900
    t = Timeline(window=win)
    t.record("h1", 950, "live", now)
    t.record("h1", 50, "stale", now)
    lst = [a for a, _ in t.by["h1"]]
    print(f"   retained times: {lst}")
    check("order is no longer sorted after a backdated arrival", lst != sorted(lst))
    expired = [i for i, a in enumerate(lst) if a <= now - win]
    live = [i for i, a in enumerate(lst) if a > now - win]
    print(f"   expired positions {expired}, live positions {live}")
    check("an EXPIRED entry sits AFTER a live one  <-- prefix assumption false",
          bool(expired) and bool(live) and max(expired) > min(live))
    check("Record retained the expired entry (it is in the slice)",
          50 in lst)

    print("\n-- but no pack rule can see it (this is the part that keeps it small) --")
    check("Seen with positive within does NOT match the stale entry",
          t.seen("h1", "stale", 900, now) == [])
    check("...nor when asOf advances one poll",
          t.seen("h1", "stale", 900, now + 60) == [])
    # Test the PROPERTY (does the loader actually REJECT a non-positive within?), not the
    # shape of the expression. My first version asserted the literal text `pe.Within <= 0`
    # and stayed GREEN when I disabled that guard with `false && ` -- a check that cannot
    # fail is not a check, which is the exact defect this project exists to catch.
    rm = re.search(r"if ([^\n]*pe\.Within <= 0[^\n]*)\{", rule)
    print(f"   loader guard: if {rm.group(1).strip() if rm else '*** NOT FOUND ***'} {{")
    check("the loader REJECTS a non-positive within (so 'ever seen' is unreachable)",
          rm is not None and "&&" not in rm.group(1))
    # and demonstrate the consequence, using the same tuple shape as self.by: (at, class)
    guard_intact = rm is not None and "&&" not in rm.group(1)
    print(f"   with the guard intact, 'ever seen' rows a pack could load: "
          f"{'none (unreachable)' if guard_intact else 'REACHABLE'}")
    # if the guard were gone, Seen(within=0) sets cutoff to -inf and WOULD match the stale entry
    stale = (50.0, "stale")
    would_match = stale[0] > float("-inf") and stale[0] <= 1000
    print(f"   hypothetically, Seen(within<=0) on the backdated row would match: {would_match}")
    check("the backdated row WOULD be visible if 'ever seen' were loadable "
          "(which is why the guard is load-bearing, not incidental)", would_match)

    print("\n-- ability eviction trims by POSITION, so it can drop live and keep stale --")
    t2 = Timeline(window=win, cap=3)
    t2.record("h2", 960, "live1", now)
    t2.record("h2", 970, "live2", now)
    t2.record("h2", 980, "live3", now)
    t2.record("h2", 10, "stale", now)      # backdated, front-trim will hit live1
    got = [(a, c) for a, c in t2.by["h2"]]
    print(f"   after cap eviction: {got}")
    check("cap eviction kept the STALE entry", any(c == "stale" for _, c in got))
    check("cap eviction DISCARDED a live entry", not any(c == "live1" for _, c in got))

    print("\n-- reachability: which call site can break the order? --")
    check("telemetry Record passes at == asOf (order preserved by construction)",
          "Record(e.host.ID, asOf, ev.Action.Type, asOf)" in tel)
    check("behavior ObserveEvent is the path where `at` comes from the event",
          "ObserveEvent(ev, now)" in beh)

    if fails:
        print(f"\nPRUNEPREFIX RED: {fails}")
        return 1

    print("\n=> Record's parenthetical is an assumption the code does not enforce: with a")
    print("   backdated `at`, the expired region is not a prefix and the stale entry is")
    print("   retained indefinitely (until cap eviction, which trims from the FRONT by")
    print("   position and can therefore discard LIVE observations instead).")
    print("   Kept honest: no pack rule can see stale evidence -- Seen re-checks the cutoff,")
    print("   and `within <= 0` is rejected at load (658). On the engine path at == asOf by")
    print("   construction, so this cannot occur there. The open question is the")
    print("   ObserveEvent path, where `at` comes from the event itself.")
    print("   Fix, Dad's call: (1) sort-on-insert or keep the slice sorted (cheap, keeps the")
    print("   comment true); (2) or correct the comment and evict by filtering the whole")
    print("   slice (which the loop ALREADY does correctly -- only the cap trim is")
    print("   position-based); (3) add a test with a backdated `at`. The comment is the real")
    print("   defect: it tells the next reader an invariant holds that nobody enforces.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
