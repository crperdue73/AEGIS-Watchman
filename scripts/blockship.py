#!/usr/bin/env python3
"""blockship — a read error in the LOG stops delivery of events already on DISK.

THE SETUP (my own words, from the code)
---------------------------------------
`BehaviorEngine.Poll` (behavior.go:390-436) is documented, correctly, as:

    "A central outage therefore costs latency, not evidence -- whatever did not
     ship rides the next cycle. The spool is the single submission path (append,
     then drain), so there is no separate direct-send branch that could
     double-deliver."

That is a good design and the comment is accurate about central outages (beat 670
confirmed the spool fsyncs and 659 confirmed Drain resumes). My beat-672 finding said
Drain's stop-on-first-failure was right and just needed a typed error.

BUT: look at the ORDER of statements in Poll.

    1. b.watch.observe()             (log health)
    2. b.timeline.Prune(now)
    3. events, err := b.eng.Matches()   <-- reads the auditd log
       if err != nil { log.Printf(...); return nil }   <-- RETURNS
    4. if len(events)>0 { spool.Append(events) }
    5. b.drain()                     <-- ships the spool

Step 3 has a **`return nil`** that sits ABOVE step 5. `Matches()` errors exactly when
`e.src.Actions()` errors (telemetry/engine.go:80-83), and `FileSource.Actions()`
(engine.go:156-187) returns an error for a genuine read failure: `os.Open` on a
permission problem, `Stat`, `Seek`, or an `io.ReadFull` error other than EOF.
`os.IsNotExist` is explicitly not an error (a host without auditd is fine), and
`size == offset` returns `nil, nil` (nothing new). So the error case is real but not
continuous: it is the ordinary transient -- EIO on a busy disk, EACCES after a
permissions drift, EMFILE/ENFILE at the fd limit, a hung NFS mount.

THE CONSEQUENCE
---------------
While that error persists, Poll returns at step 3 **every cycle**. The spool is never
drained. The spool already contains everything from every prior cycle that central had
not yet accepted -- and the comment's promise ("whatever did not ship rides the next
cycle") is false in this state, because there is no next cycle for the SPOOL: each
cycle dies before reaching drain.

So: one failing READ (the sensor is broken) halts DELIVERY (the durable, already-captured
evidence). Those are independent concerns and they are chained in series.

This is the same family as beats 670 and 671, one layer up:
    670: the disk write fails          -> the batch is lost, silently
    671: a poison record wedges Drain  -> the backlog stops, silently
    673: a read error stops Poll early -> the backlog stops, but this time it is
                                          *reported* (there IS a log line) and still
                                          *harmless to evidence*, because the events
                                          are on disk and will ship when reads recover.

Be precise about severity -- this is NOT beat 670. Nothing is lost here. The events are
in the spool; the moment `Actions()` succeeds again, Poll reaches drain() and ships the
whole backlog. So the honest statement is:

    A broken sensor delays delivery of good evidence indefinitely, and the delay has no
    bound, because its length is the length of the read outage -- and a read outage can
    be permanent (auditd rotated the file to a path the collector cannot open) while
    central is perfectly healthy and the operator sees a quiet, green fleet view.

That last clause is the interesting one. Combine this with beat 669 (the wire cannot say
"I missed something") and beat 667 (healthz cannot say no): the collector is failing to
read its only input, therefore producing zero matches, therefore appearing as a healthy
agent with nothing to report. Three separate findings that each individually said
"silence looks like calm", and this is the path that joins them: read failure -> no
events -> no append -> no drain -> quiet host -> absent fleet row.

CLAIM / NOT CLAIMED
-------------------
CLAIMED (read from source): the `return nil` on `Matches()` error precedes the `drain()`
    call, so an Actions() error skips draining in that cycle; Actions() returns an error
    for Open-with-errnotexist-excluded/Stat/Seek/ReadFull failures; no other poll path
    drains the spool; `drain()` is called only from Poll.
NOT CLAIMED: that any event is LOST by this path -- it is not, and saying so would repeat
    the error beat 670 was careful to avoid. **NOT CLAIMED: that a read error is common.**
    I did not measure EIO rates; the argument is that the state is *reachable and
    unbounded*, not that it is *frequent*. **Also NOT CLAIMED: that Drain has an
    independent timer.** I checked: it does not (see below), which is what makes the
    delay unbounded rather than one cycle.
"""

import sys
import pathlib
import re


def strip_comments(src):
    """Remove // and /* */ comments -- a mechanism is an identifier, not a sentence (beat 669).

    Ten findings in this project were contaminated by treating prose as code. Functionally:
    the docstring at behavior.go:386 literally contains the words 'spool' and 'drain', and a
    naive search for 'does anything else drain' would match the COMMENT explaining that
    nothing else drains. Anchor on identifiers only.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    src = re.sub(r"\s//\s.*$", "", src, flags=re.M)
    return src


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    beh_p = root / "internal/collector/behavior.go"
    tel_p = root / "internal/telemetry/engine.go"
    fails = 0

    def check(name, ok):
        nonlocal fails
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            fails += 1

    try:
        raw = beh_p.read_text()
        tel = tel_p.read_text()
        sub = strip_comments(tel)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read input ({e}); a missing input is not a pass")
        return 1
    code = strip_comments(raw)

    # --- Poll's body, in code order ---
    poll_start = code.index("func (b *BehaviorEngine) Poll()")
    poll = code[poll_start:]
    poll = poll[:poll.index("\n}\n")]

    print("-- Poll's control flow, comments stripped --")
    i_matches = poll.find("b.eng.Matches()")
    i_early = poll.find("return nil", i_matches)
    i_drain = poll.find("b.drain()")
    print(f"   Matches() at {i_matches}, early return at {i_early}, drain() at {i_drain}")
    check("Poll calls eng.Matches()", i_matches > 0)
    check("Poll calls drain()", i_drain > 0)
    check("a `return nil` sits BETWEEN Matches() and drain()  <-- the defect",
          i_matches < i_early < i_drain)

    # the early return must be the error branch, not an unrelated one
    between = poll[i_matches:i_early]
    check("that return is guarded by `if err != nil`", "if err != nil" in between)
    check("that branch LOGS before returning (so it is not silent)",
          "log.Printf" in between)

    print("\n-- drain() is reachable only through Poll --")
    drains = re.findall(r"\.drain\(\)", code)
    print(f"   occurrences of .drain() in behavior.go (code only): {len(drains)}")
    check("drain() is called exactly once", len(drains) == 1)
    # nothing else in the tree drives draining on a timer
    others = []
    for p in (root / "internal").rglob("*.go"):
        t = strip_comments(p.read_text())
        if re.search(r"\.drain\(\)", t) and p != beh_p:
            others.append(str(p.relative_to(root)))
    print(f"   other files calling .drain(): {others or 'none'}")
    check("no other file calls drain()", not others)
    timers = re.findall(r"time\.NewTicker|time\.Tick\(|time\.After\(", code)
    print(f"   timers/sleeps in behavior.go (code only): {timers or 'none'}")
    check("Poll/drain own no independent timer -- the delay is unbounded, not one cycle",
          not timers)

    print("\n-- Matches() errors only when the SOURCE fails --")
    m = sub[sub.index("func (e *Engine) Matches()"):]
    m = m[:m.index("\n}\n")]
    check("Matches() returns the error from e.src.Actions()",
          "e.src.Actions()" in m and "return nil, err" in m)
    check("Matches() has exactly one error return (the source read)",
          m.count("return nil, err") == 1)

    print("\n-- FileSource.Actions() error cases --")
    a = sub[sub.index("func (f *FileSource) Actions()"):]
    a = a[:a.index("\n}\n")]
    for label, frag in (
        ("Open error is returned unless NotExist", "os.IsNotExist" in a),
        ("Stat error is returned", "fh.Stat()" in a),
        ("Seek error is returned", "fh.Seek(" in a),
        ("ReadFull error is returned (beyond EOF)", "io.ReadFull(" in a),
    ):
        check(label, frag)
    err_returns = len(re.findall(r"return nil, err", a))
    print(f"   `return nil, err` count in Actions(): {err_returns}")
    check("Actions() has at least 4 real error returns (this is not a dead branch)",
          err_returns >= 4)
    check("a MISSING log is NOT an error (so this is not 'no auditd' noise)",
          "if os.IsNotExist(err)" in a and "return nil, nil" in a)

    print("\n-- the promise the comment makes, and where it is false --")
    check("Poll's docstring promises 'whatever did not ship rides the next cycle'",
          "rides the next cycle" in raw)
    check("...but that promise is conditional on reaching drain(), which the early return skips",
          i_early < i_drain)

    if fails:
        print(f"\nBLOCKSHIP RED: {fails}")
        return 1

    print("\n=> A transient or permanent failure to READ the audit log makes Poll return")
    print("   before drain(), so events already fsynced to the spool stop being delivered.")
    print("   Nothing is LOST -- this is not beat 670. The delay is just unbounded, because")
    print("   drain() has no timer of its own and Poll is its only caller.")
    print("   The severity is the combination: read failure -> zero matches -> quiet host ->")
    print("   absent fleet row (669) -> healthz says ok (667). Four findings, one screen.")
    print("   Fix, Dad's call: move the drain ABOVE the Matches() error return, or (cleaner)")
    print("   drain in a `defer` so shipping is not conditional on the sensor working. One")
    print("   design note: draining before append would ship the backlog a cycle earlier and")
    print("   is arguably more correct -- backlog is older than this cycle's events.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
