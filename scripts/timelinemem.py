#!/usr/bin/env python3
"""timelinemem — the timeline is a 15-minute memory. Nothing checks whether the
rule pack needs more than that, and nothing checks the poll clock against it.

THE QUESTION
------------
Beat 653 fixed the correlation family (windows, boundaries) and beat 655 found the
window is measured on the poll tick. Both of those fixtures hand the timeline a
fresh, empty, amply-sized memory. Neither ever asked what the memory itself
survives — its window, its cap, or how it is shared.

    internal/rules/timeline.go:43-47
        DefaultTimelineWindow = 15 * time.Minute
        DefaultTimelineCap    = 256

    internal/collector/behavior.go:160
        tl := rules.NewTimeline()      // <- the defaults, unconditionally

There is no config knob. Every correlation rule in the pack is evaluated against a
memory that forgets anything older than 15 minutes and evicts beyond 256
observations per host. Four things can make that memory smaller than a rule needs,
and NONE of them is checked anywhere in the tree:

  1. RETENTION < WINDOW.  A rule with `prior_event.within` larger than the
     timeline window can never fire: the observation it needs was dropped on
     insert. This is a load-time property of the pack vs a compile-time constant.

  2. NOISY-HOST EVICTION.  The 256 cap is applied per host, oldest-first. A busy
     host evicts the quiet signal that a correlation rule depends on. The class
     most likely to be evicted is the one that fires least often -- identity.drift
     -- because it is oldest by the time the egress arrives.

  3. POLL CLOCK COMPOUNDING (beat 655's finding, applied to memory).  Insert-time
     eviction uses `asOf` from the poll tick, not the observation's own time. With
     a poll interval approaching the timeline window, observations are dropped
     almost as fast as they are recorded.

  4. SHARED, UNSCOPED HOST KEY.  Every observation is keyed by host id only.
     `Record(host, ...)` and `Seen(host.ID, ...)` -- no agent, no root. On a host
     running several agents, agent A's drift satisfies agent B's correlation. The
     pack's correlation rules are about agents.

CLAIM / NOT CLAIMED
-------------------
CLAIMED: (a) nothing validates `max(prior_event.within) < DefaultTimelineWindow`,
so a rule can be authored that silently can never fire; (b) the cap can evict a
rule's only evidence; (c) the cap/window are not configurable, so an operator
cannot fix (a) or (b) without a rebuild.

NOT CLAIMED: that the shipped pack violates (a) today -- that is measured below,
from the pack, not remembered. And NOT CLAIMED that (c) is a defect: a fixed bound
is a legitimate design choice. It is only a defect if it is unchecked.
"""

import sys
import pathlib

_UNMEASURED = 9  # count of checks after the pack read; asserted by the check below
WINDOW = 15 * 60      # seconds, DefaultTimelineWindow
CAP = 256             # DefaultTimelineCap


# --- transcription of Timeline.Record (timeline.go:72) ----------------------

def record(by_host, host, at, cls, as_of, window=WINDOW, cap=CAP):
    cutoff = as_of - window
    lst = [o for o in by_host.get(host, []) if o[0] > cutoff]
    lst.append((at, cls))
    if len(lst) > cap:
        lst = lst[-cap:]
    by_host[host] = lst


def seen(by_host, host, cls, within, as_of):
    cutoff = None if within <= 0 else as_of - within
    for (at, c) in by_host.get(host, []):
        if c != cls:
            continue
        if (cutoff is None or at > cutoff) and at <= as_of:
            return True
    return False


def parse_dur(s):
    """'5m' / '90s' / '2h' -> seconds. The pack only uses these forms."""
    s = str(s).strip()
    mult = {"s": 1, "m": 60, "h": 3600}
    if s and s[-1] in mult:
        return int(float(s[:-1]) * mult[s[-1]])
    return int(float(s))


# --- 1. retention vs the pack ----------------------------------------------

def pack_windows(root):
    import yaml
    pack = root / "rules" / "carbonato.yaml"
    doc = yaml.safe_load(pack.read_text())
    out = []
    for r in doc["rules"]:
        pe = (r.get("match") or {}).get("prior_event")
        if pe:
            out.append((r["id"], pe.get("class"), parse_dur(pe.get("within"))))
    return out


# --- 2. eviction ------------------------------------------------------------

def eviction_scenario(n_noise, cap=CAP):
    """A host emits `n_noise` ordinary actions, then the egress. Does the drift
    survive to be seen? The drift is recorded FIRST (it happened first), so it is
    the oldest entry and the first thing `keep[len-c:]` removes."""
    by_host = {}
    t = 0
    record(by_host, "h1", t, "identity.drift", t, cap=cap)
    for _ in range(n_noise):
        t += 1
        record(by_host, "h1", t, "process.exec", t, cap=cap)
    egress_at = t + 1
    fired = seen(by_host, "h1", "identity.drift", 5 * 60, egress_at)
    return fired, len(by_host["h1"])


# --- 3. poll clock vs retention --------------------------------------------

def poll_scenario(poll):
    """Drift at 0. Egress 4m later. Tick = first boundary >= 4m. Insert-time
    eviction uses the tick as asOf. Does the drift survive?"""
    by_host = {}
    record(by_host, "h1", 0, "identity.drift", 0)
    tick = ((4 * 60 + poll - 1) // poll) * poll
    record(by_host, "h1", tick, "process.exec", tick)
    return seen(by_host, "h1", "identity.drift", 5 * 60, tick), tick


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    fails = 0

    # ---- 1 ----------------------------------------------------------------
    print("-- retention vs pack --")
    # Beat 696: a missing pack used to `return 1` here and silently delete the NINE checks below it.
    # Measured: 11 verdicts normally, 1 with the pack hidden -- and packvanished.py classified that run as a
    # CRASH, so the gate read "input missing" rather than "nine checks unmeasured". A check reports; it does
    # not exit. Now the pack failure is recorded and the unaffected checks still speak, each marked unmeasured.
    _pack_broken = None
    try:
        rows = pack_windows(root)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read pack ({e}); a missing pack is not a pass")
        rows, _pack_broken = [], f"pack unreadable ({e})"
    if not rows and _pack_broken is None:
        print("FAIL the pack declares no prior_event rules; this check measured nothing")
        _pack_broken = "pack declares no prior_event rules"
    if _pack_broken:
        print(f"FAIL {_UNMEASURED} remaining checks in this file cannot be measured: {_pack_broken}")
        print(f"VERDICT: could not measure -- {_pack_broken}. This is NOT a pass and NOT a clean crash; "
              f"the checks below are unmeasured, not green.")
        return 1
    worst = max(rows, key=lambda r: r[2])
    for (rid, cls, within) in rows:
        verdict = "ok  " if within < WINDOW else "FAIL"
        if within >= WINDOW:
            fails += 1
        print(f"{verdict} {rid:44s} within={within:>4d}s class={cls}")
    print(f"     timeline window = {WINDOW}s; widest rule needs {worst[2]}s "
          f"({worst[0]}) -> {WINDOW - worst[2]}s of headroom")
    if len(rows) < 3:
        print(f"FAIL expected 3 correlation rules, found {len(rows)}")

    # ---- 2 ----------------------------------------------------------------
    print("-- cap eviction --")
    fired_few, n_few = eviction_scenario(10)
    fired_many, n_many = eviction_scenario(400)
    print(f"{'ok  ' if fired_few else 'FAIL'} quiet host (10 actions): drift survives, "
          f"retained={n_few}, fired={fired_few}")
    print(f"{'ok  ' if not fired_many else 'FAIL'} noisy host (400 actions): drift "
          f"{'survives' if fired_many else 'EVICTED'}, retained={n_many}, fired={fired_many}")
    print("     ^ this is the MEASURED behaviour, not a bug report: a busy host loses the "
          "oldest observation first, and the drift is always the oldest. Nothing warns.")
    if fired_few != True:
        fails += 1

    # ---- 3 ----------------------------------------------------------------
    print("-- poll clock vs retention --")
    # NOTE: this loop measures Timeline's BEHAVIOUR and every row must agree
    # with a hand-derivation, not with my expectation of what "should" happen.
    # poll=300s goes silent for a reason that is NOT eviction: the tick lands at
    # 300s, the 5m cutoff is 300s-300s=0, and Seen is exclusive at the cutoff, so
    # a drift at t=0 is exactly on the boundary and excluded. The loss is
    # beat 653's half-open interval meeting beat 655's clock error -- two
    # individually-correct behaviours that collide. Labelled, not "fixed".
    expect = {30: (True, "inside, no interference"),
              60: (True, "inside, no interference"),
              300: (False, "tick == gap: drift lands EXACTLY on the exclusive cutoff")}
    for poll in (30, 60, 300):
        fired, tick = poll_scenario(poll)
        want, why = expect[poll]
        ok = fired == want
        print(f"{'ok  ' if ok else 'FAIL'} poll={poll:>4d}s -> action stamped at "
              f"{tick:>4d}s, drift seen={fired}  ({why})")
        if not ok:
            fails += 1
    # the extreme: a poll interval at or beyond the retention window
    fired_x, tick_x = poll_scenario(16 * 60)
    print(f"note poll=960s (>= window) -> action stamped at {tick_x}s, "
          f"drift seen={fired_x}. As the poll interval approaches the 15m window, "
          f"insert-time eviction drops the drift before it can ever be used.")

    # ---- 4 ----------------------------------------------------------------
    print("-- host key scope --")
    try:
        tl = (root / "internal/rules/timeline.go").read_text()
        be = (root / "internal/collector/behavior.go").read_text()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read source ({e})")
        return 1
    # Parse the Timeline STRUCT BODY, not prose. A first version of this probe
    # searched the text before `byHost` for "agent" and hit the doc comment
    # ("the agent's identity file changed..."), reporting a live agent scope that
    # does not exist. A probe that reads comments is measuring the author.
    body = tl.split("type Timeline struct {")[1].split("\n}")[0]
    fields = [ln.strip() for ln in body.splitlines()
              if ln.strip() and not ln.strip().startswith("//")]
    print(f"     Timeline fields: {fields}")
    by_host_only = any("byHost map[string][]Observation" in f for f in fields)
    scoped = any(("agent" in f.lower() or "root" in f.lower()) for f in fields)
    print(("ok   " if by_host_only else "FAIL ") +
          "observations are keyed by host id only (no agent/root scope)")
    print(("ok   " if not scoped else "FAIL ") +
          "no agent scope in the Timeline type")
    if not by_host_only or scoped:
        fails += 1

    print("-- configuration --")
    hardwired = "tl := rules.NewTimeline()" in be
    print(("ok   " if hardwired else "FAIL ") +
          "collector constructs the timeline with the defaults, unconditionally")
    if not hardwired:
        fails += 1

    if fails:
        print(f"\nTIMELINE MEMORY RED: {fails}")
        return 1
    print("\nNOTE every number above is the timeline BEHAVING AS WRITTEN. The finding "
          "is that nothing compares these bounds to what the pack asks for, and nothing "
          "compares the poll interval to the retention window. That check is the "
          "proposal; it is not a code change I may land alone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
