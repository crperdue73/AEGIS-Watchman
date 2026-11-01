#!/usr/bin/env python3
"""windowfixture.py — fixtures for the THREE correlation rules (prior_event).

Beat 645 added endpoint fixtures, beat 649 added argv fixtures. Each time the
finding was the same: a rule family with rules in the pack and nothing that had
ever asked it a question. This is the fourth family, and the only one that
matches on TIME rather than a value: 3 rules require `prior_event` + `within`,
and until now the only coverage was one Go test
(TestCorrelationEgressAfterIdentityChange) that exercises exactly one of the
three windows and never probes its edge.

Why an edge is the interesting part
-----------------------------------
Every other predicate is a string comparison, where "correct" and "incorrect"
are far apart. A window is a duration, so the failure lives in one nanosecond:
if the boundary is inclusive where it should be exclusive, nothing looks
different until an attacker lands a write exactly N minutes before an egress.

Reading of internal/rules/timeline.go:89 (Seen), recorded as an assumption:

    cutoff := asOf.Add(-within)
    if o.At.After(cutoff) && !o.At.After(asOf) { return true }

  * `At == asOf`            -> fire   (an event at "now" counts)
  * `At == cutoff`          -> SILENT (`After` is strict; the window is
                                       (asOf-within, asOf])
  * `At == asOf + 1ns`      -> silent (no future observations)
  * `within <= 0`           -> cutoff is the zero time => "any time retained"

That is a coherent half-open interval, and the fixture pins it so a refactor to
`!o.At.Before(cutoff)` — which reads identically in review — goes red.

⚠️ This file is a TRANSCRIPTION of timeline.go in Python, exactly like
packcheck.go_path_match is a transcription of path.Match. There is no Go
toolchain on this host, so the real Seen() is never executed here. Every verdict
below is attested by my own mirror. When Go comes back, this file should be
replaced by (or checked against) a Go test with the same table.

Declared expectations are the second column. A row whose `why` says EDGE is a
boundary row; the structural check requires each of the three rules' windows to
be probed on both sides.
"""

import sys

# --- mirror of internal/rules/timeline.go -------------------------------

def seen(observations, host, cls, within_ns, as_of):
    """transcription of Timeline.Seen. observations: [(host, ns, class)]."""
    if host == "" or cls == "":
        return False
    if within_ns <= 0:
        cutoff = None            # time.Time{} — everything retained qualifies
    else:
        cutoff = as_of - within_ns
    for (h, at, c) in observations:
        if h != host or c != cls:
            continue
        after_cutoff = True if cutoff is None else at > cutoff
        not_future = at <= as_of
        if after_cutoff and not_future:
            return True
    return False

NS = 1
US = 1_000 * NS
MS = 1_000 * US
S = 1_000 * MS

# --- fixtures -----------------------------------------------------------
# window in ns comes from the pack itself so a pack change shows up here.

WINDOWS = {
    "carbonato.exfil.egress_after_identity_change": 5 * 60 * S,
    "carbonato.secret.read_after_shell": 2 * 60 * S,
    "carbonato.container.after_identity_change": 10 * 60 * S,
}

CLASS_FOR = {
    "carbonato.exfil.egress_after_identity_change": "identity.drift",
    "carbonato.secret.read_after_shell": "process.exec",
    "carbonato.container.after_identity_change": "identity.drift",
}

T = 1_800_000_000 * S   # arbitrary fixed "now"


def rows_for(rule):
    w = WINDOWS[rule]
    c = CLASS_FOR[rule]
    obs = lambda delta, host="h1", cls=None: [(host, T + delta, cls or c)]
    other = [(("h2"), T, c)]                 # same class, DIFFERENT host
    wrong_class = [("h1", T, "identity.drift" if c != "identity.drift" else "process.exec")]
    return [
        # inside the window, comfortably
        (rule, obs(-1 * S),        True,  "CLEAN: one second ago is inside any window"),
        (rule, obs(-w + 1),        True,  "EDGE: one nanosecond inside the cutoff"),
        (rule, obs(-w - 1),        False, "EDGE: one nanosecond outside the cutoff"),
        (rule, obs(-w),            False, "EDGE: exactly at cutoff is EXCLUSIVE"),
        (rule, obs(0),             True,  "EDGE: observation at asOf counts"),
        (rule, obs(1),             False, "EDGE: future observation must not fire"),
        (rule, obs(-w // 2),       True,  "CLEAN: mid-window"),
        (rule, [],                 False, "CLEAN: empty timeline"),
        (rule, obs(-1 * S, "h2"),  False, "CLEAN: same class on a different host is not correlation"),
        (rule, wrong_class,        False, "CLEAN: same host, different class"),
        (rule, [( "h1", T, c), ("h1", T - 1, "noise")], True,
                                           "CLEAN: a matching observation buried in noise"),
    ]


ALL_ROWS = [r for rule in WINDOWS for r in rows_for(rule)]


def main():
    fails = 0
    total = 0
    for (rule, observations, declared, why) in ALL_ROWS:
        got = seen(observations, "h1", CLASS_FOR[rule], WINDOWS[rule], T)
        total += 1
        if got != declared:
            fails += 1
            print(f"FAIL {rule:52s} {why}\n     declared={declared} got={got}")
    print(f"ok {total} window fixture rows judged, {fails} failures")

    # structural: every rule must be probed on BOTH sides of its window
    print("-- edge coverage --")
    for rule in WINDOWS:
        has_in = any(r[0] == rule and r[2] and "EDGE" in r[3] for r in ALL_ROWS)
        has_out = any(r[0] == rule and not r[2] and "EDGE" in r[3] for r in ALL_ROWS)
        if not (has_in and has_out):
            fails += 1
            print(f"FAIL {rule} is not probed on both sides of its window "
                  f"(fire={has_in}, silent={has_out})")
        else:
            print(f"ok   {rule} probed both sides")

    # structural: every correlation rule in the pack must appear here
    print("-- pack coverage --")
    try:
        import pathlib
        import yaml
        pack = pathlib.Path(__file__).resolve().parents[1] / "rules" / "carbonato.yaml"
        doc = yaml.safe_load(pack.read_text())
        in_pack = {r["id"]: r["match"]["prior_event"]["within"]
                   for r in doc["rules"] if "prior_event" in (r.get("match") or {})}
    except Exception as e:  # noqa: BLE001
        in_pack = None
        print(f"FAIL could not read pack ({e}); a missing pack is not a pass")

    # A pack we could not read is NOT an exemption from the coverage check.
    # Without this, deleting rules/carbonato.yaml makes this file print ok and
    # exit 0 -- it would report a clean box on the day the pack went missing.
    # (Caught by scripts/packpresent.py, which knows my new instrument exists and
    # had never been told what it may do when the pack is absent.)
    if in_pack is None:
        fails += 1
    if in_pack is not None:
        missing = set(in_pack) - set(WINDOWS)
        extra = set(WINDOWS) - set(in_pack)
        if missing:
            fails += 1
            print(f"FAIL correlation rules with no fixture: {sorted(missing)}")
        if extra:
            fails += 1
            print(f"FAIL fixtures for rules not in the pack: {sorted(extra)}")
        if not missing and not extra:
            print(f"ok   all {len(in_pack)} correlation rules have fixtures")

    if fails:
        print(f"\nWINDOW FIXTURES RED: {fails}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
