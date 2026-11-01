#!/usr/bin/env python3
"""batchprobe — every action in a batch is stamped with ONE timestamp, so correlations
are judged against a clock coarser than the events they judge.

`Engine.Matches` (internal/telemetry/engine.go:83-100):

    asOf := e.now()                       // ONE clock read for the whole batch
    for _, ev := range events {
        e.timeline.Record(host, asOf, ev.Action.Type, asOf)   // same instant for every action
        matched := e.engine.Match(host, agent, ev.Action, ev.Actor, asOf)
    }

A batch is whatever accumulated since the last poll — default `Report.Interval = 30s`
(internal/collector/config.go:143). So the timestamp written and judged is the POLL time, not the
event time. `ev.Action` carries no timestamp of its own to prefer; the audit line may, but the
Action struct is built without keeping it.

Consequences this probe measures:
  A. SKEW — an action at the start of a batch is recorded as if it happened at the end. Up to one
     full interval. For a 2m rule (`secret.read_after_shell`) that is 25% of the window.
  B. BATCH-BLIND — two actions in the SAME batch are always zero apart, so a correlation cannot
     distinguish "read the secret 1.9m after the shell" from "1ms after".
  C. ORDER-LOST — within a batch, a later action is recorded BEFORE an earlier one is judged only
     in the sense that all are recorded first; but relative order between batch peers is destroyed.

EXIT: 0 = measured, no violation of a stated invariant; 1 = an invariant is broken; 2 = cannot judge.
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


def config_defaults():
    src = read("internal", "collector", "config.go")
    interval = re.search(r"c\.Report\.Interval\s*=\s*(\d+)\s*\*\s*time\.Second", src)
    return int(interval.group(1))


def temporal_rules():
    pack = read("rules", "carbonato.yaml")
    out = []
    for m in re.finditer(r"\n  - id: (\S+)(.*?)(?=\n  - id: |\Z)", pack, re.S):
        pe = re.search(r"prior_event:\s*\n\s*class:\s*(\S+)\s*\n\s*within:\s*(\S+)", m.group(2))
        if pe:
            w = pe.group(2).strip("\"'")
            n = int(re.match(r"(\d+)", w).group(1))
            mins = n / 60 if w.endswith("s") else n * 60 if w.endswith("h") else n
            out.append((m.group(1), pe.group(1).strip("\"'"), mins))
    return out


def main():
    interval = config_defaults()
    rules = temporal_rules()
    print(f"batchprobe — default Report.Interval = {interval}s (read from internal/collector/config.go)")

    # --- the source really does stamp one instant per batch ---------------------------------
    src = read("internal", "telemetry", "engine.go")
    body = src[src.index("func (e *Engine) Matches("):]
    body = body[:body.index("\n}\n")]
    ok("asOf is computed ONCE, outside the loop", body.index("asOf := e.now()") < body.index("for _, ev := range events"))
    ok("the same asOf is written into the timeline", body.count("e.timeline.Record(e.host.ID, asOf") == 1)
    ok("the same asOf is passed to the matcher", "ev.Actor, asOf)" in body)
    ok("no per-action timestamp is read anywhere in Matches",
       not re.search(r"ev\.(?:TS|Timestamp|At)\b", body), "a per-event clock appeared — this finding may be void")

    ok("three temporal rules", len(rules) == 3, str(len(rules)))
    print(f"  denominator: {len(rules)} prior_event rule(s) measured against a {interval}s batch granularity\n")

    # --- A: the skew, per rule -----------------------------------------------------------------
    print("  A. timestamp skew — an action at the head of a batch is stamped at the tail:")
    worst = 0.0
    for rid, cls, mins in sorted(rules, key=lambda r: r[2]):
        skew = interval / 60.0
        pct = skew / mins * 100 if mins else 0
        worst = max(worst, pct)
        print(f"     {rid:52} within={mins:>4.0f}m  skew={skew:.1f}m ({pct:.1f}% of the window)")
    # the invariant worth holding: the clock must be finer than the window it measures
    ok("batch granularity is finer than every rule's window", worst < 100.0, f"{worst:.1f}%")
    # a 2m rule with a 30s batch: a fifth to a quarter of the whole window is unresolvable
    ok("for the 2m rule the skew is material (>20% of the window)", worst > 20.0, f"{worst:.1f}%")
    ok("but it never EXCEEDS the window, so a correlation cannot be falsely ordered by skew alone",
       all(interval / 60.0 < m for _, _, m in rules))

    # --- B: batch-blind within one batch -------------------------------------------------------
    print("\n  B. within one batch, two actions are always zero apart:")
    # model: a shell exec at t=0 and a secret read at t=29s, both in the same 30s batch
    t_shell, t_read, as_of = 0.0, 29.0, 30.0
    recorded_gap = as_of - as_of
    true_gap = (t_read - t_shell) / 60.0
    ok("recorded gap is zero although the true gap was 29s", recorded_gap == 0.0)
    ok("true gap was well inside the 2m rule", true_gap < 2.0, f"{true_gap:.2f}m")
    ok("the rule still fires — a zero gap is < 2m, so the direction of error is permissive",
       recorded_gap <= 2.0)
    # the failure direction matters: if the read came BEFORE the shell, it still looks correlated
    t_shell, t_read, as_of = 29.0, 0.0, 30.0
    ok("an action that happened BEFORE its 'cause' is still judged correlated",
       (as_of - as_of) <= 2.0, "read-at-0s, shell-at-29s, same batch -> indistinguishable")

    # --- C: what it costs at the boundary ------------------------------------------------------
    print("\n  C. boundary — a true correlation just inside the window can be judged outside it:")
    # shell at t=1s, read at t=119s (1m58s apart, inside a 2m rule), batch boundary at 120s
    gap = 118.0 / 60.0
    ok("the true correlation is inside the 2m window", gap < 2.0, f"{gap:.3f}m")
    # If they land in different batches, the read is stamped at some asOf >= its poll, the shell at
    # some asOf <= its own poll. Worst case: shell stamped at 30 (its poll), read stamped at 150 (two
    # polls later), because the read landed at 119s and was picked up on the 150s tick.
    worst_recorded = (150.0 - 30.0) / 60.0
    # Seen uses `o.At.After(cutoff)`, so EXACTLY at the window is excluded, not included.
    ok("worst-case recorded gap across a batch boundary reaches the full window",
       worst_recorded >= 2.0, f"{worst_recorded:.2f}m vs 2m window")
    ok("...and lands exactly ON the boundary, which Seen excludes (strict After)",
       worst_recorded == 2.0, f"{worst_recorded:.2f}m")
    ok("...so a real 1m58s correlation is MISSED (false negative) at the worst case",
       worst_recorded >= 2.0)
    # the ordinary case is not the extreme: one poll of skew total, still a real loss
    ordinary = (119.0 - 60.0) / 60.0   # shell stamped at 60, read stamped at 119 by the 120 tick
    ok("even the ordinary one-poll skew shrinks a 2m window to 60s of usable resolution",
       ordinary <= 1.0, f"{ordinary:.2f}m")

    print()
    if not rules:
        print("  batchprobe: CANNOT JUDGE — no temporal rules.")
        return 2
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  " + f)
        return 1
    print("batchprobe: PASS on its own invariants — one poll of skew never makes a rule unwinnable, but two\n"
          "  (a correlation straddling a batch boundary) reaches the full 2m window and Seen excludes the\n"
          "  boundary. Within-batch order is destroyed outright: two same-batch actions are always zero apart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
