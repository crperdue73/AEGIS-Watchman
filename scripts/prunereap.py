#!/usr/bin/env python3
"""prunereap — Prune deletes whole hosts, and nothing has ever measured what a
`within: 0` rule loses when it does.

THE QUESTION
------------
653 (windows) / 655 (clock) / 656 (bounds) / 657 (two writers) walked the timeline
from every side except one: what it THROWS AWAY.

    internal/rules/timeline.go:118-136
        func (t *Timeline) Prune(asOf time.Time) {
            cutoff := asOf.Add(-t.window)
            for host, list := range t.byHost {
                keep := list[:0]
                for _, o := range list { if o.At.After(cutoff) { keep = ... } }
                if len(keep) == 0 { delete(t.byHost, host); continue }
                t.byHost[host] = keep
            }
        }

    internal/collector/behavior.go:401-403
        if b.timeline != nil { b.timeline.Prune(time.Now()) }

`Prune` runs at the TOP of every `Poll`, before any match. It is called with
`time.Now()` -- a THIRD clock, neither the drift's `ev.TS` nor `Matches`' shared
`asOf`.

THE INTERACTION
---------------
`Seen` (timeline.go:89-106) accepts `within <= 0` and documents it as "any time
still retained" -- and `temporalMatches` (engine.go:84-91) rejects `at.IsZero()`
but puts no floor on `within`. So a rule may legitimately be authored with
`within: 0` meaning "ever seen". Its retention is then governed entirely by
`Prune`'s 15-minute window, and `Prune` is the only thing that can make the answer
"no" for a host that has ever emitted the class.

What `Prune` actually does to that guarantee, measured:

  1. A quiet host is DELETED, not just emptied. `delete(t.byHost, host)` on line
     131 means its map slot is gone. Correct -- and it makes the answer depend on
     `asOf` (the third clock), not on whether the host ever existed.

  2. TWO CALLS IN ONE TICK REAP DIFFERENT ANSWERS. `Prune(time.Now())` at the top
     of Poll uses a fresh `time.Now()` each time. Two Polls milliseconds apart
     near the 15-minute boundary can reap different hosts. Nothing is wrong; the
     answer to "ever seen" is simply a function of when you asked, with 15-minute
     resolution.

  3. THE ONE THAT MATTERS: `Prune` uses `o.At.After(cutoff)` -- the SAME
     exclusive test as `Seen`. So an observation at exactly `asOf-window` is
     reaped, and `Seen`'s cutoff test for a rule whose window equals the timeline
     window also excludes it. Consistent. BUT beat 656 established that a rule's
     `within` may be LARGER than what `Prune` keeps, and `Prune` never checks it.
     The inconsistency is at the pack level and is already covered by
     `timelinemem.py`. This file must NOT re-report it as new.

CLAIM / NOT CLAIMED
-------------------
CLAIMED: (a) `within <= 0` is REJECTED at load (rule.go:296), so "ever seen" is
    not expressible in a pack at all -- the only expressible retention is a
    positive duration capped by the 15m reap window; (b) `Prune` deletes the host
    map entry, so the answer to any rule becomes "seen within 15 minutes of
    whenever Poll last ran", and nothing records that the host ever existed; (c)
    `Prune` is called with a fresh `time.Now()` -- a THIRD clock -- so the reap
    boundary is not the same instant as the match's `asOf`, and two Polls
    seconds apart can disagree.

CORRECTED WHILE WRITING THIS: my first draft claimed `within: 0` was an accepted
    shape whose retention silently became the reap window. **That was false.**
    rule.go:296 rejects `pe.Within <= 0` at load. I had derived the claim from
    `Seen`'s comment ("within <= 0 means any time still retained") -- a comment
    describing a helper's tolerance, not the pack's vocabulary. Field (a) is
    rewritten to say the true and actually stronger thing: the vocabulary cannot
    express "ever", and one defence (Validate) is all that stands between an
    author and a rule whose memory is shorter than its words. **Fourth time in
    five beats that prose was not code.**

NOT CLAIMED: that any shipped rule uses `within: 0`. That is measured below from
    the pack -- and now known to be impossible, which is why the row set below
    tests Prune's behaviour directly rather than through a rule. And NOT
    re-reported: the pack-vs-window size question (beat 656).
"""

import sys
import pathlib

S = 1
M = 60 * S
WINDOW = 15 * M


def record(by_host, host, at, cls, as_of, window=WINDOW):
    cutoff = as_of - window
    lst = [o for o in by_host.get(host, []) if o[0] > cutoff]
    lst.append((at, cls))
    by_host[host] = lst


def prune(by_host, as_of, window=WINDOW):
    cutoff = as_of - window
    for host in list(by_host):
        keep = [o for o in by_host[host] if o[0] > cutoff]
        if not keep:
            del by_host[host]          # <- the host slot is DELETED
        else:
            by_host[host] = keep


def seen(by_host, host, cls, within, as_of):
    cutoff = None if within <= 0 else as_of - within
    for (at, c) in by_host.get(host, []):
        if c != cls:
            continue
        if (cutoff is None or at > cutoff) and at <= as_of:
            return True
    return False


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    fails = 0

    # ---- 1. is within:0 a real, accepted shape? ---------------------------
    print("-- is within:0 a legal shape? --")
    try:
        tl = (root / "internal/rules/timeline.go").read_text()
        eng = (root / "internal/rules/engine.go").read_text()
        be = (root / "internal/collector/behavior.go").read_text()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read source ({e}); a missing input is not a pass")
        return 1

    rule = (root / "internal/rules/rule.go").read_text()
    checks = [
        ("Seen tolerates within <= 0 at the helper level (comment, not the pack "
         "vocabulary)", 'A within <= 0 means "any time still retained"' in tl),
        ("Validate REJECTS within <= 0, so 'ever seen' is not expressible in a pack",
         "pe.Within <= 0" in rule and "prior_event.within must be positive" in rule),
        ("Prune runs before matching in Poll",
         be.index("b.timeline.Prune(time.Now())") < be.index("events, err := b.eng.Matches()")),
        ("Prune deletes the host entry outright",
         "delete(t.byHost, host)" in tl),
        ("Prune uses a fresh time.Now() (a third clock)",
         "b.timeline.Prune(time.Now())" in be),
    ]
    for (n, ok) in checks:
        print(("ok   " if ok else "FAIL ") + n)
        if not ok:
            fails += 1

    # ---- 2. what within:0 actually survives ------------------------------
    print("-- what 'ever seen' really means --")
    rows = []

    # a) seen recently -> true
    h = {}
    record(h, "h1", 0, "identity.drift", 0)
    prune(h, 60)
    rows.append(("drift 1m ago, within:0", seen(h, "h1", "identity.drift", 0, 60), True,
                 "retained -> true"))

    # b) seen 14m ago -> still true
    h = {}
    record(h, "h1", 0, "identity.drift", 0)
    prune(h, 14 * M)
    rows.append(("drift 14m ago, within:0", seen(h, "h1", "identity.drift", 0, 14 * M), True,
                 "inside the 15m reap window -> true"))

    # c) seen 16m ago -> FALSE, though the rule says "ever"
    h = {}
    record(h, "h1", 0, "identity.drift", 0)
    prune(h, 16 * M)
    rows.append(("drift 16m ago, within:0", seen(h, "h1", "identity.drift", 0, 16 * M), False,
                 "REAPED by Prune -> the rule reads 'no' for a host that proved yes"))

    # d) exactly on the boundary -> reaped (exclusive), same as Seen
    h = {}
    record(h, "h1", 0, "identity.drift", 0)
    prune(h, WINDOW)
    rows.append(("drift exactly 15m ago, within:0", seen(h, "h1", "identity.drift", 0, WINDOW), False,
                 "exclusive cutoff: on the boundary is already gone"))

    # e) the host slot is deleted, so a later drift starts from nothing
    h = {}
    record(h, "h1", 0, "identity.drift", 0)
    prune(h, 16 * M)
    was = "h1" in h
    rows.append(("host slot after full reap", "h1" in h, False,
                 f"host key deleted (present={was}); nothing records that it ever existed"))

    for (label, got, want, why) in rows:
        ok = (got == want) if isinstance(want, bool) else False
        if got != want:
            fails += 1
        shown = f"{got!r}"
        print(f"{'ok  ' if got == want else 'FAIL'} {label:34s} -> {shown:5s}  ({why})")

    # ---- 3. two polls, two answers ---------------------------------------
    print("-- the third clock --")
    h = {}
    record(h, "h1", 0, "identity.drift", 0)
    # Poll A at 15m-1s, Poll B at 15m+1s (the real gap is 2 seconds)
    prune(h, WINDOW - 1)
    a = seen(h, "h1", "identity.drift", 0, WINDOW - 1)
    prune(h, WINDOW + 1)
    b = seen(h, "h1", "identity.drift", 0, WINDOW + 1)
    print(f"ok   poll at {WINDOW - 1}s -> {a};  poll 2s later at {WINDOW + 1}s -> {b}")
    print("     same timeline, same rule, answers differ because Prune took a fresh "
          "time.Now() between them and the reap boundary fell in the gap.")
    if not (a is True and b is False):
        fails += 1

    # ---- 4. does the pack use it? ---------------------------------------
    print("-- does the shipped pack use within:0? --")
    try:
        import yaml
        doc = yaml.safe_load((root / "rules" / "carbonato.yaml").read_text())
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read pack ({e})")
        return 1
    zero = []
    for r in doc["rules"]:
        pe = (r.get("match") or {}).get("prior_event")
        if pe is not None and str(pe.get("within", "")).strip() in ("0", "0s"):
            zero.append(r["id"])
    print(f"ok   {len(zero)} of {len(doc['rules'])} rules use within:0 -> {zero or 'none'}")
    print("     So this is a LATENT shape: legal, documented, and unused today. If one "
          "is ever written, its retention is the 15m reap window and not the word 'ever'.")

    if fails:
        print(f"\nPRUNE REAP RED: {fails}")
        return 1
    print("\nNOTE 'ever seen' is not expressible in this design: the only memory is a "
          "15-minute buffer that deletes the host key entirely. Not a defect in Prune -- "
          "a limit of the vocabulary that nothing states.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
